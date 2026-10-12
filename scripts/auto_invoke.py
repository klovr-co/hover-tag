"""Decide whether an untagged Slack message is addressed to Tag and a reply is welcome.

Two providers ask the same question. TypeSafe Jev answers a ``noul`` question
with true/false criteria about a structured ``state``. OpenAI Decisions answers a
``predicate`` question whose instructions carry the same criteria, about the
same state serialized into ``input``. Each provider has its own key, model and
threshold. Every failure, refusal or malformed answer raises, and callers stay
silent.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_CONTEXT_MESSAGES = 10
MAX_MESSAGE_CHARS = 2_000
QUESTION_NAME = "tag_is_addressed"
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


class AutoInvokeError(RuntimeError):
    """The decision could not be obtained or validated; the caller stays silent."""


class AutoInvokeRefusal(AutoInvokeError):
    """The provider declined to answer; treated exactly like any other failure."""


@dataclass(frozen=True)
class AutoInvokeDecision:
    should_invoke: bool
    tag_is_addressed: float
    model: str
    provider: str


@dataclass(frozen=True)
class Score:
    """One provider answer before a threshold is applied."""
    probability: float
    model: str
    input_tokens: int | None = None


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    url: str
    key_setting: str
    model_setting: str
    threshold_setting: str
    default_model: str
    default_threshold: float
    # Input price in USD per million tokens, used only for evaluation estimates.
    usd_per_million_input: float
    price_source: str
    payload: Callable[[dict[str, Any], str], dict[str, Any]]
    parse: Callable[[Any, str], Score]


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
        raise AutoInvokeError("the provider returned an invalid probability")
    return float(value)


def _input_tokens(result: dict[str, Any]) -> int | None:
    usage = result.get("usage")
    tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
    return tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None


def jev_payload(state: dict[str, Any], model: str) -> dict[str, Any]:
    return {
        "state": state,
        "model": model,
        "questions": {
            QUESTION_NAME: {
                "type": "noul",
                "instructions": INSTRUCTIONS,
                "criteria": {"true": TRUE_CRITERIA, "false": FALSE_CRITERIA},
            },
        },
    }


def jev_parse(result: Any, _model: str) -> Score:
    try:
        probability = result["answers"][QUESTION_NAME]["noul"]
        response_model = result["model"]
    except (KeyError, TypeError) as exc:
        raise AutoInvokeError("TypeSafe returned an incomplete decision") from exc
    if not isinstance(response_model, str) or not response_model:
        raise AutoInvokeError("TypeSafe returned an invalid decision")
    return Score(_probability(probability), response_model, _input_tokens(result))


DECISIONS_INSTRUCTIONS = (
    f"{INSTRUCTIONS}\n\nAnswer true when: {TRUE_CRITERIA}\n\nAnswer false when: {FALSE_CRITERIA}"
)
DECISIONS_INPUT_PREFIX = (
    "Slack conversation state as JSON. `thread_history` is oldest first and excludes "
    "`current_message`, which is the message to judge.\n"
)


def decisions_input(state: dict[str, Any]) -> str:
    # The fixed preamble comes first and the per-message state last.
    return DECISIONS_INPUT_PREFIX + json.dumps(state, ensure_ascii=False)


def decisions_payload(state: dict[str, Any], model: str) -> dict[str, Any]:
    return {
        "model": model,
        "input": decisions_input(state),
        "questions": [
            {"type": "predicate", "name": QUESTION_NAME, "instructions": DECISIONS_INSTRUCTIONS},
        ],
    }


def decisions_parse(result: Any, model: str) -> Score:
    answers = result.get("answers") if isinstance(result, dict) else None
    if not isinstance(answers, list):
        raise AutoInvokeError("OpenAI returned an incomplete decision")
    matches = [a for a in answers if isinstance(a, dict) and a.get("name") == QUESTION_NAME]
    if len(matches) != 1:
        raise AutoInvokeError("OpenAI returned an incomplete decision")
    answer = matches[0]
    if answer.get("type") == "refusal":
        raise AutoInvokeRefusal("OpenAI refused to decide")
    if answer.get("type") != "predicate":
        raise AutoInvokeError("OpenAI returned an invalid decision")
    # The guide documents only `answers`; report the requested model when no other is given.
    response_model = result.get("model")
    return Score(
        _probability(answer.get("probability")),
        response_model if isinstance(response_model, str) and response_model else model,
        _input_tokens(result),
    )


PROVIDERS: dict[str, Provider] = {
    "typesafe-jev": Provider(
        name="typesafe-jev",
        label="TypeSafe Jev",
        url="https://api.typesafe.ai/v1/systemone",
        key_setting="OPENTAG_TYPESAFE_API_KEY",
        model_setting="OPENTAG_JEV_MODEL",
        threshold_setting="OPENTAG_JEV_THRESHOLD",
        default_model="jev-latest",
        default_threshold=0.9,
        usd_per_million_input=0.042,
        price_source="input price supplied with this change, not on docs.typesafe.ai/api.md; output price unknown",
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
) -> Score:
    """Ask one provider for the probability that Tag is addressed and a reply is welcome."""
    try:
        spec = PROVIDERS[provider]
    except KeyError:
        raise AutoInvokeError(f"Unknown classifier provider {provider!r}") from None
    if not api_key:
        raise AutoInvokeError(f"{spec.key_setting} is not set")
    model = model or spec.default_model
    request = urllib.request.Request(
        spec.url,
        data=json.dumps(spec.payload(state, model)).encode("utf-8"),
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
        raise AutoInvokeError(f"{spec.label} request failed: {type(exc).__name__}") from exc
    return spec.parse(result, model)


def evaluate(
    state: dict[str, Any],
    *,
    provider: str,
    api_key: str,
    threshold: float,
    model: str = "",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> AutoInvokeDecision:
    """Decide whether to invoke Tag; raises AutoInvokeError on any failure."""
    answer = score(state, provider=provider, api_key=api_key, model=model, timeout=timeout)
    return AutoInvokeDecision(
        should_invoke=answer.probability >= threshold,
        tag_is_addressed=answer.probability,
        model=answer.model,
        provider=provider,
    )


@dataclass(frozen=True)
class Settings:
    provider: str
    api_key: str
    model: str
    threshold: float
    timeout: float


def settings_from(environment: Mapping[str, str] = os.environ) -> Settings:
    """Read the selected provider's settings; a missing key or bad value raises."""
    provider = environment.get("OPENTAG_AUTO_INVOKE_PROVIDER", "") or DEFAULT_PROVIDER
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise AutoInvokeError(f"Unknown classifier provider {provider!r}")
    api_key = environment.get(spec.key_setting, "").strip()
    if not api_key:
        raise AutoInvokeError(f"{spec.key_setting} is not set")
    try:
        threshold = float(environment.get(spec.threshold_setting, "") or spec.default_threshold)
        timeout = float(environment.get("OPENTAG_AUTO_INVOKE_TIMEOUT_SECONDS", "") or DEFAULT_TIMEOUT_SECONDS)
    except ValueError:
        raise AutoInvokeError("Classifier threshold and timeout must be numbers") from None
    if not 0.5 <= threshold <= 1 or not 0 < timeout <= 30:
        raise AutoInvokeError("Classifier threshold or timeout is out of range")
    return Settings(
        provider=provider,
        api_key=api_key,
        model=environment.get(spec.model_setting, "") or spec.default_model,
        threshold=threshold,
        timeout=timeout,
    )
