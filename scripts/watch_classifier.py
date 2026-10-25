"""Decide whether an untagged Slack message is addressed to Tag and a reply is welcome.

Two providers ask the same question. TypeSafe Jev answers a ``noul`` question
with true/false criteria about a structured ``state``. OpenAI Decisions answers a
``predicate`` question whose instructions carry the same criteria, about the
same state serialized into ``input``. Each provider has its own key, model and
threshold. In chime-in channels a second question, whether the post invites
colleagues' reactions, can go in the same call with its own threshold. Every
failure, refusal or malformed answer raises, and callers stay silent.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any


DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_CONTEXT_MESSAGES = 10
MAX_MESSAGE_CHARS = 2_000
QUESTION_NAME = "tag_is_addressed"
CHIME_QUESTION_NAME = "invites_reactions"
DEFAULT_CHIME_THRESHOLD = 0.95
SLACK_USER_MENTION_RE = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]+)?>")

INSTRUCTIONS = (
    "Considering `current_message`, `thread_history`, and `assistant`, is the "
    "requester talking to the assistant, directly or as part of the addressed "
    "audience, such that an assistant reply is welcome now? Read the conversation "
    "in chronological order, using author roles to distinguish `tag`, `requester`, "
    "`participant`, and `another_bot`. Determine who the requester is engaging "
    "with across the conversation, not only who sent the immediately previous message."
)
TRUE_CRITERIA = (
    "The message addresses the assistant by name; continues an exchange with "
    "the assistant; or asks the whole channel or an inclusive audience such as "
    "'you all', 'everyone', or 'anyone' for a response; or otherwise speaks "
    "to the personal assistant expecting it to join the conversation. Questions, "
    "opinions, greetings, and requests can all qualify; a concrete task or plea "
    "for help is not required. When the assistant is the most recent conversational "
    "partner, short fragments, corrections, reactions, and continuations are true. "
    "An exchange with Tag can span several consecutive requester messages: "
    "for example, after Tag offers a draft, 'make it shorter' followed by "
    "'and more casual' can both continue that exchange. Tag does not have to "
    "have authored the immediately previous message. Use the meaning and "
    "sequence of the conversation to establish that the exchange is still with Tag."
)
FALSE_CRITERIA = (
    "The message is directed only to a named human or clearly human-only group; "
    "merely mentions or discusses the assistant; is narration or an aside with "
    "no reply invited; or belongs to a human conversation where the assistant "
    "is not being addressed. When the recent exchange is with `another_bot` or a "
    "`participant`, and the current message naturally replies to that party rather "
    "than `tag`, it is false. The assistant's ability to help does not make it part "
    "of the audience. Tag appearing somewhere earlier in the thread is not enough. "
    "Check whether the conversation has shifted to another person or bot; do not "
    "carry forward an old exchange with Tag after that shift unless the requester "
    "addresses Tag again or clearly resumes that exchange."
)
ASSISTANT_ROLE = (
    "The requester's personal AI assistant and a participant in this Slack "
    "channel. It can converse, offer opinions, answer questions, and perform "
    "tasks through the owner's local coding agent and connected tools."
)


CHIME_INSTRUCTIONS = (
    "Considering `current_message`, `thread_history`, and `assistant`, does the requester's "
    "current message invite colleagues' reactions, such that a short, warm comment from the "
    "assistant as a coworker would be welcome now? Judge only the current message, using the "
    "thread for context. Nobody has to address the assistant."
)
CHIME_TRUE_CRITERIA = (
    "The message shares news the team would celebrate or comment on: a launch, release, demo, "
    "milestone, finished piece of work, win, customer success, or similar good news, posted for "
    "the channel to see. A link, image or video can be the news itself when the words present it "
    "as something to look at."
)
CHIME_FALSE_CRITERIA = (
    "The message is a request, a question to someone, a task assignment, an incident, outage or "
    "problem report, routine status or logistics, a reminder, a meeting note, or an aside; it "
    "shares sensitive personal news such as illness, loss, health, family or job changes; it is "
    "part of a conversation between other people; or it is already addressed to the assistant. "
    "When unsure, it is false: a missed comment costs little, an unwelcome one costs trust."
)


@dataclass(frozen=True)
class Question:
    name: str
    instructions: str
    true: str
    false: str


ADDRESSED = Question(QUESTION_NAME, INSTRUCTIONS, TRUE_CRITERIA, FALSE_CRITERIA)
CHIME_IN = Question(CHIME_QUESTION_NAME, CHIME_INSTRUCTIONS, CHIME_TRUE_CRITERIA, CHIME_FALSE_CRITERIA)
QUESTIONS = {question.name: question for question in (ADDRESSED, CHIME_IN)}


class ClassifierError(RuntimeError):
    """The decision could not be obtained or validated; the caller stays silent."""


class ClassifierRefusal(ClassifierError):
    """The provider declined to answer; treated exactly like any other failure."""


@dataclass(frozen=True)
class ClassifierDecision:
    should_invoke: bool
    tag_is_addressed: float | None
    model: str
    provider: str
    # Set only when the chime-in question was asked.
    invites_reactions: float | None = None
    should_chime: bool = False


@dataclass(frozen=True)
class Score:
    """One provider answer before a threshold is applied."""
    probability: float
    model: str
    input_tokens: int | None = None
    cached_tokens: int | None = None
    # Every asked question's probability; ``probability`` is the first question's.
    probabilities: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    url: str
    key_setting: str
    model_setting: str
    threshold_setting: str
    chime_threshold_setting: str
    default_model: str
    default_threshold: float
    # Input price in USD per million tokens, used only for evaluation estimates.
    usd_per_million_input: float
    price_source: str
    payload: Callable[..., dict[str, Any]]
    parse: Callable[..., Score]


def minimize_text_identities(
    text: str,
    *,
    requester_user_id: str,
    assistant_user_id: str,
) -> str:
    """Replace Slack member IDs embedded in message text with semantic roles."""
    def replacement(match: re.Match[str]) -> str:
        user_id = match.group(1)
        if assistant_user_id and user_id == assistant_user_id:
            return "<tag>"
        if requester_user_id and user_id == requester_user_id:
            return "<requester>"
        return "<participant>"

    return SLACK_USER_MENTION_RE.sub(replacement, text)


MAX_THREAD_PAGES = 5


def thread_window(messages: list[dict[str, Any]], current_ts: str) -> list[dict[str, Any]]:
    """The thread's first message plus the latest messages before the current one, oldest first.

    The classifier needs both what the thread is about and the exchange the current
    message answers; the opening replies of a long thread are the least useful part.
    """
    ordered = sorted((m for m in messages if isinstance(m, dict) and m.get("ts") != current_ts),
                     key=lambda m: float(m.get("ts") or 0))
    before = [m for m in ordered if float(m.get("ts") or 0) < float(current_ts or "inf")]
    if len(before) <= MAX_CONTEXT_MESSAGES:
        return before
    return [before[0], *before[-(MAX_CONTEXT_MESSAGES - 1):]]


def fetch_thread_window(client: Any, channel: str, thread_ts: str, current_ts: str) -> list[dict[str, Any]]:
    """Read the thread up to the current message, page by page, and keep the window."""
    messages: list[dict[str, Any]] = []
    cursor = None
    for _ in range(MAX_THREAD_PAGES):
        response = client.conversations_replies(
            channel=channel, ts=thread_ts, latest=current_ts, inclusive=True, limit=200,
            **({"cursor": cursor} if cursor else {}))
        messages += [m for m in response.get("messages", []) if isinstance(m, dict)]
        cursor = (response.get("response_metadata") or {}).get("next_cursor")
        if not response.get("has_more") or not cursor:
            break
    return thread_window(messages, current_ts)


def message_state(
    event: dict[str, Any],
    thread_messages: list[dict[str, Any]],
    *,
    assistant_name: str,
    assistant_user_id: str = "",
) -> dict[str, Any]:
    """Build bounded, identity-minimized state for the routing decision."""
    requester = event.get("user", "")
    current_ts = event.get("ts", "")
    history: list[dict[str, str]] = []
    for message in thread_messages[-MAX_CONTEXT_MESSAGES:]:
        text = message.get("text")
        if not isinstance(text, str) or not text.strip() or message.get("ts") == current_ts:
            continue
        author = "participant"
        if assistant_user_id and message.get("user") == assistant_user_id:
            author = "tag"
        elif message.get("user") == requester:
            author = "requester"
        elif message.get("bot_id"):
            author = "another_bot"
        history.append(
            {
                "author": author,
                "text": minimize_text_identities(
                    text,
                    requester_user_id=requester,
                    assistant_user_id=assistant_user_id,
                )[:MAX_MESSAGE_CHARS],
            }
        )
    return {
        "assistant": {"name": assistant_name, "role": ASSISTANT_ROLE},
        "current_message": minimize_text_identities(
            str(event.get("text") or ""),
            requester_user_id=requester,
            assistant_user_id=assistant_user_id,
        )[:MAX_MESSAGE_CHARS],
        "thread_history": history,
    }


def _probability(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ClassifierError("the provider returned an invalid probability")
    return float(value)


def _input_tokens(result: dict[str, Any]) -> int | None:
    usage = result.get("usage")
    tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
    return tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None


def cached_tokens(result: Any) -> int | None:
    """Cached input tokens, when the provider reports them (OpenAI style or flat)."""
    usage = result.get("usage") if isinstance(result, dict) else None
    if not isinstance(usage, dict):
        return None
    details = usage.get("input_tokens_details")
    value = details.get("cached_tokens") if isinstance(details, dict) else usage.get("cached_tokens")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _score(probabilities: dict[str, float], model: str, result: dict[str, Any]) -> Score:
    return Score(next(iter(probabilities.values())), model, _input_tokens(result), probabilities=probabilities)


def jev_payload(state: dict[str, Any], model: str, questions: tuple[Question, ...] = (ADDRESSED,)) -> dict[str, Any]:
    return {
        "state": state,
        "model": model,
        "questions": {
            question.name: {
                "type": "noul",
                "instructions": question.instructions,
                "criteria": {"true": question.true, "false": question.false},
            }
            for question in questions
        },
    }


def jev_parse(result: Any, _model: str, questions: tuple[Question, ...] = (ADDRESSED,)) -> Score:
    try:
        probabilities = {q.name: _probability(result["answers"][q.name]["noul"]) for q in questions}
        response_model = result["model"]
    except (KeyError, TypeError) as exc:
        raise ClassifierError("TypeSafe returned an incomplete decision") from exc
    if not isinstance(response_model, str) or not response_model:
        raise ClassifierError("TypeSafe returned an invalid decision")
    return _score(probabilities, response_model, result)


def decisions_instructions(question: Question) -> str:
    return f"{question.instructions}\n\nAnswer true when: {question.true}\n\nAnswer false when: {question.false}"


DECISIONS_INSTRUCTIONS = decisions_instructions(ADDRESSED)
DECISIONS_INPUT_PREFIX = (
    "Slack conversation state as JSON. `thread_history` is oldest first and excludes "
    "`current_message`, which is the message to judge.\n"
)


def decisions_input(state: dict[str, Any]) -> str:
    # The fixed preamble comes first and the per-message state last.
    return DECISIONS_INPUT_PREFIX + json.dumps(state, ensure_ascii=False)


def decisions_payload(state: dict[str, Any], model: str, questions: tuple[Question, ...] = (ADDRESSED,)) -> dict[str, Any]:
    return {
        "model": model,
        "input": decisions_input(state),
        "questions": [
            {"type": "predicate", "name": question.name, "instructions": decisions_instructions(question)}
            for question in questions
        ],
    }


def decisions_parse(result: Any, model: str, questions: tuple[Question, ...] = (ADDRESSED,)) -> Score:
    answers = result.get("answers") if isinstance(result, dict) else None
    if not isinstance(answers, list):
        raise ClassifierError("OpenAI returned an incomplete decision")
    probabilities = {}
    for question in questions:
        matches = [a for a in answers if isinstance(a, dict) and a.get("name") == question.name]
        if len(matches) != 1:
            raise ClassifierError("OpenAI returned an incomplete decision")
        answer = matches[0]
        if answer.get("type") == "refusal":
            raise ClassifierRefusal("OpenAI refused to decide")
        if answer.get("type") != "predicate":
            raise ClassifierError("OpenAI returned an invalid decision")
        probabilities[question.name] = _probability(answer.get("probability"))
    # The guide documents only `answers`; report the requested model when no other is given.
    response_model = result.get("model")
    return _score(probabilities, response_model if isinstance(response_model, str) and response_model else model,
                  result)


PROVIDERS: dict[str, Provider] = {
    "typesafe-jev": Provider(
        name="typesafe-jev",
        label="TypeSafe Jev",
        url="https://api.typesafe.ai/v1/systemone",
        key_setting="OPENTAG_TYPESAFE_API_KEY",
        model_setting="OPENTAG_JEV_MODEL",
        threshold_setting="OPENTAG_JEV_THRESHOLD",
        chime_threshold_setting="OPENTAG_JEV_CHIME_THRESHOLD",
        default_model="jev-latest",
        default_threshold=0.9,
        usd_per_million_input=0.042,
        price_source="https://docs.typesafe.ai/models.md (jev-1.13.0; output free)",
        payload=jev_payload,
        parse=jev_parse,
    ),
    "openai-decisions": Provider(
        name="openai-decisions",
        label="OpenAI Decisions",
        url="https://api.openai.com/v1/decisions",
        key_setting="OPENTAG_OPENAI_DECISIONS_API_KEY",
        model_setting="OPENTAG_DECISIONS_MODEL",
        threshold_setting="OPENTAG_DECISIONS_THRESHOLD",
        chime_threshold_setting="OPENTAG_DECISIONS_CHIME_THRESHOLD",
        default_model="gpt-6-luna",
        default_threshold=0.9,
        usd_per_million_input=0.10,
        price_source="https://developers.openai.com/api/docs/guides/decisions (output free)",
        payload=decisions_payload,
        parse=decisions_parse,
    ),
}
DEFAULT_PROVIDER = "typesafe-jev"


def score(
    state: dict[str, Any],
    *,
    provider: str,
    api_key: str,
    model: str = "",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    payload: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
    parse: Callable[[Any, str], Score] | None = None,
    questions: tuple[Question, ...] = (ADDRESSED,),
) -> Score:
    """Ask one provider each question's probability in a single call.

    By default that is whether Tag is addressed and a reply is welcome. ``payload`` and ``parse`` let
    offline evaluations try other prompts for that one question; production uses the defaults.
    """
    try:
        spec = PROVIDERS[provider]
    except KeyError:
        raise ClassifierError(f"Unknown classifier provider {provider!r}") from None
    if not api_key:
        raise ClassifierError(f"{spec.key_setting} is not set")
    model = model or spec.default_model
    request = urllib.request.Request(
        spec.url,
        data=json.dumps(payload(state, model) if payload else spec.payload(state, model, questions)).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - callers fail closed on any provider failure
        raise ClassifierError(f"{spec.label} request failed: {type(exc).__name__}") from exc
    answer = parse(result, model) if parse else spec.parse(result, model, questions)
    cached = cached_tokens(result)
    return answer if cached is None else replace(answer, cached_tokens=cached)


def evaluate(
    state: dict[str, Any],
    *,
    provider: str,
    api_key: str,
    threshold: float,
    model: str = "",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    addressed: bool = True,
    chime_in: bool = False,
    chime_threshold: float = DEFAULT_CHIME_THRESHOLD,
) -> ClassifierDecision:
    """Decide whether to invoke Tag, and whether to chime in; raises ClassifierError on any failure.

    The asked questions go in one call. A question that wasn't asked never passes."""
    questions = tuple(q for q, asked in ((ADDRESSED, addressed), (CHIME_IN, chime_in)) if asked)
    if not questions:
        raise ClassifierError("No classifier question was asked")
    answer = score(state, provider=provider, api_key=api_key, model=model, timeout=timeout, questions=questions)
    addressed_p = answer.probabilities.get(QUESTION_NAME)
    chime_p = answer.probabilities.get(CHIME_QUESTION_NAME)
    return ClassifierDecision(
        should_invoke=addressed_p is not None and addressed_p >= threshold,
        tag_is_addressed=addressed_p,
        model=answer.model,
        provider=provider,
        invites_reactions=chime_p,
        should_chime=chime_p is not None and chime_p >= chime_threshold,
    )


@dataclass(frozen=True)
class Settings:
    provider: str
    api_key: str
    model: str
    threshold: float
    timeout: float
    chime_threshold: float = DEFAULT_CHIME_THRESHOLD


def settings_from(environment: Mapping[str, str] = os.environ) -> Settings:
    """Read the selected provider's settings; a missing key or bad value raises."""
    provider = environment.get("OPENTAG_WATCH_CLASSIFIER", "") or DEFAULT_PROVIDER
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise ClassifierError(f"Unknown classifier provider {provider!r}")
    api_key = environment.get(spec.key_setting, "").strip()
    if not api_key:
        raise ClassifierError(f"{spec.key_setting} is not set")
    try:
        threshold = float(environment.get(spec.threshold_setting, "") or spec.default_threshold)
        chime_threshold = float(environment.get(spec.chime_threshold_setting, "") or DEFAULT_CHIME_THRESHOLD)
        timeout = float(environment.get("OPENTAG_WATCH_CLASSIFIER_TIMEOUT_SECONDS", "") or DEFAULT_TIMEOUT_SECONDS)
    except ValueError:
        raise ClassifierError("Classifier threshold and timeout must be numbers") from None
    if not 0.5 <= threshold <= 1 or not 0.5 <= chime_threshold <= 1 or not 0 < timeout <= 30:
        raise ClassifierError("Classifier threshold or timeout is out of range")
    return Settings(
        provider=provider,
        api_key=api_key,
        model=environment.get(spec.model_setting, "") or spec.default_model,
        threshold=threshold,
        timeout=timeout,
        chime_threshold=chime_threshold,
    )
