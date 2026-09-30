"""Use TypeSafe Jev to detect when the owner is talking to Tag in Slack."""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from typing import Any


SYSTEM_ONE_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
DEFAULT_THRESHOLD = 0.9
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_CONTEXT_MESSAGES = 10
MAX_MESSAGE_CHARS = 2_000
SLACK_USER_MENTION_RE = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]+)?>")


class JevEvaluationError(RuntimeError):
    """The Jev decision could not be obtained or validated."""


@dataclass(frozen=True)
class AutoInvokeDecision:
    should_invoke: bool
    tag_is_addressed: float
    model: str


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
    """Build bounded, identity-minimized state for the Jev routing decision."""
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
        "assistant": {
            "name": assistant_name,
            "role": (
                "The requester's personal AI assistant and a participant in this Slack "
                "channel. It can converse, offer opinions, answer questions, and perform "
                "tasks through the owner's local coding agent and connected tools."
            ),
        },
        "current_message": minimize_text_identities(
            str(event.get("text") or ""),
            requester_user_id=requester,
            assistant_user_id=assistant_user_id,
        )[:MAX_MESSAGE_CHARS],
        "thread_history": history,
    }


def evaluate(
    state: dict[str, Any],
    *,
    api_key: str,
    threshold: float = DEFAULT_THRESHOLD,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> AutoInvokeDecision:
    """Ask Jev whether the owner is addressing Tag and a reply is welcome now."""
    payload = {
        "state": state,
        "model": model,
        "questions": {
            "tag_is_addressed": {
                "type": "noul",
                "instructions": (
                    "Considering `current_message`, `thread_history`, and `assistant`, is the "
                    "requester talking to the assistant, directly or as part of the addressed "
                    "audience, such that an assistant reply is welcome now? Read the conversation "
                    "in chronological order, using author roles to distinguish `tag`, `requester`, "
                    "`participant`, and `another_bot`. Determine who the requester is engaging "
                    "with across the conversation, not only who sent the immediately previous message."
                ),
                "criteria": {
                    "true": (
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
                    ),
                    "false": (
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
                    ),
                },
            },
        },
    }
    request = urllib.request.Request(
        SYSTEM_ONE_URL,
        data=json.dumps(payload).encode("utf-8"),
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
        raise JevEvaluationError(f"TypeSafe request failed: {type(exc).__name__}") from exc

    try:
        answers = result["answers"]
        tag_is_addressed = answers["tag_is_addressed"]["noul"]
        response_model = result["model"]
    except (KeyError, TypeError) as exc:
        raise JevEvaluationError("TypeSafe returned an incomplete decision") from exc
    if (
        isinstance(tag_is_addressed, bool)
        or not isinstance(tag_is_addressed, (int, float))
        or not 0 <= tag_is_addressed <= 1
        or not isinstance(response_model, str)
        or not response_model
    ):
        raise JevEvaluationError("TypeSafe returned an invalid decision")

    return AutoInvokeDecision(
        should_invoke=float(tag_is_addressed) >= threshold,
        tag_is_addressed=float(tag_is_addressed),
        model=response_model,
    )
