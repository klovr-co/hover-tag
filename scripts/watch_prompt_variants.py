"""Prompt variants for the channel-watching classifier, for offline evaluation only.

Production uses the baseline prompt in ``watch_classifier``. Each variant here
builds a request payload and parses the answer for one provider. Fixed text
comes first and the per-message state last, so repeated requests share a prefix
the provider can cache. The Decisions API rejects ``prompt_cache_key``
(HTTP 400, unknown_parameter); it caches automatically and reports
``usage.input_tokens_details.cached_tokens``.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

try:
    from scripts import watch_classifier as wc
except ImportError:
    import watch_classifier as wc


SHORT_QUESTION = (
    "Considering `current_message` and `thread_history`, is the requester talking to the `assistant`, "
    "directly or as part of an inclusive audience, so that a reply from it is welcome now?"
)
SHORT_TRUE = [
    "It names the assistant, or continues the requester's exchange with it, including short follow-ups.",
    "It asks an inclusive audience such as 'anyone', 'everyone' or 'you all' for a response.",
    "It greets, asks or requests something of the assistant; no concrete task is needed.",
]
SHORT_FALSE = [
    "It is aimed at a named person, a human-only group, or another bot.",
    "It only mentions or discusses the assistant, or uses 'tag' as an ordinary word.",
    "It is a note, aside or announcement that invites no reply.",
    "The conversation has moved on from the assistant.",
]

ADDRESSED_QUESTION = (
    "Considering `current_message` and `thread_history`, is the requester talking to the `assistant`, "
    "directly or as part of an inclusive audience that includes it? Read the conversation in order and use "
    "the author roles to see who the requester is engaging with."
)
ADDRESSED_TRUE = (
    "It names the assistant; continues an exchange with the assistant (short follow-ups and corrections count "
    "when the assistant is the recent partner); or speaks to an inclusive audience such as 'anyone', "
    "'everyone' or 'you all'."
)
ADDRESSED_FALSE = (
    "It is aimed only at a named person, a human-only group or another bot; it merely mentions or discusses "
    "the assistant; or the conversation has moved on to someone else."
)
WELCOME_QUESTION = (
    "Considering `current_message` and `thread_history`, would a reply to `current_message` be welcome right "
    "now: does it ask, request, propose or otherwise invite a response?"
)
WELCOME_TRUE = "It asks a question, makes a request, asks for an opinion, or continues a discussion that expects an answer."
WELCOME_FALSE = (
    "It is a note to self, an aside, an announcement, thanks or an acknowledgement that closes the exchange, "
    "or a reply would clearly interrupt."
)

FEW_SHOT = wc.ADDRESSED_EXAMPLES


# The baseline criteria, rewritten as standalone items with the same content.
BULLET_TRUE = [
    "The message addresses the assistant by name.",
    "The message continues an exchange with the assistant.",
    "The message asks the whole channel or an inclusive audience such as 'you all', 'everyone' or 'anyone' for a response.",
    "The message otherwise speaks to the personal assistant, expecting it to join the conversation.",
    "Questions, opinions, greetings and requests all qualify; no concrete task or plea for help is needed.",
    "When the assistant is the most recent conversational partner, short fragments, corrections, reactions and continuations are true.",
    "An exchange with the assistant can span several consecutive requester messages, such as 'make it shorter' then 'and more casual' after a draft.",
    "The assistant does not have to have authored the immediately previous message.",
]
BULLET_FALSE = [
    "The message is directed only to a named human or a clearly human-only group.",
    "The message merely mentions or discusses the assistant.",
    "The message is narration or an aside with no reply invited.",
    "The message belongs to a human conversation where the assistant is not addressed.",
    "The recent exchange is with `another_bot` or a `participant`, and the message naturally replies to them.",
    "The assistant's ability to help does not make it part of the audience.",
    "The assistant appearing earlier in the thread is not enough once the conversation has shifted to another person or bot, unless the requester addresses it again.",
]


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


# ---- TypeSafe Jev -------------------------------------------------------------------------------------------

def _jev(questions: dict[str, Any]) -> Callable[[dict[str, Any], str], dict[str, Any]]:
    def payload(state: dict[str, Any], model: str) -> dict[str, Any]:
        return {"model": model, "questions": questions, "state": state}  # Fixed text before state.
    return payload


def _jev_min(names: list[str]) -> Callable[[Any, str], wc.Score]:
    def parse(result: Any, _model: str) -> wc.Score:
        try:
            values = [result["answers"][name]["noul"] for name in names]
            response_model = result["model"]
        except (KeyError, TypeError) as exc:
            raise wc.ClassifierError("TypeSafe returned an incomplete decision") from exc
        if not isinstance(response_model, str) or not response_model:
            raise wc.ClassifierError("TypeSafe returned an invalid decision")
        return wc.Score(min(wc._probability(v) for v in values), response_model, wc._input_tokens(result))
    return parse


# ---- OpenAI Decisions -----------------------------------------------------------------------------------------

def _decisions(questions: list[dict[str, Any]]) -> Callable[[dict[str, Any], str], dict[str, Any]]:
    def payload(state: dict[str, Any], model: str) -> dict[str, Any]:
        return {"model": model, "questions": questions, "input": wc.decisions_input(state)}
    return payload


def _decisions_min(names: list[str]) -> Callable[[Any, str], wc.Score]:
    def parse(result: Any, model: str) -> wc.Score:
        answers = result.get("answers") if isinstance(result, dict) else None
        if not isinstance(answers, list):
            raise wc.ClassifierError("OpenAI returned an incomplete decision")
        values = []
        for name in names:
            matches = [a for a in answers if isinstance(a, dict) and a.get("name") == name]
            if len(matches) != 1:
                raise wc.ClassifierError("OpenAI returned an incomplete decision")
            if matches[0].get("type") == "refusal":
                raise wc.ClassifierRefusal("OpenAI refused to decide")
            if matches[0].get("type") != "predicate":
                raise wc.ClassifierError("OpenAI returned an invalid decision")
            values.append(wc._probability(matches[0].get("probability")))
        response_model = result.get("model")
        return wc.Score(min(values), response_model if isinstance(response_model, str) and response_model else model,
                        wc._input_tokens(result))
    return parse


def _predicate(name: str, instructions: str) -> dict[str, Any]:
    return {"type": "predicate", "name": name, "instructions": instructions}


Q = wc.QUESTION_NAME
VARIANTS: dict[str, dict[str, tuple]] = {
    "baseline": {
        # The original prompt. Production Decisions has used "few-shot" since 26 Oct 2026; Jev still uses this.
        "typesafe-jev": (wc.jev_payload, wc.jev_parse),
        "openai-decisions": (_decisions([_predicate(Q, wc.DECISIONS_INSTRUCTIONS)]),
                             wc.decisions_parse),
    },
    "short": {
        "typesafe-jev": (_jev({Q: {"type": "noul", "instructions": SHORT_QUESTION,
                                   "criteria": {"true": _bullets(SHORT_TRUE), "false": _bullets(SHORT_FALSE)}}}),
                         _jev_min([Q])),
        "openai-decisions": (_decisions([_predicate(Q, f"{SHORT_QUESTION}\nTrue when:\n{_bullets(SHORT_TRUE)}"
                                                      f"\nFalse when:\n{_bullets(SHORT_FALSE)}")]),
                             _decisions_min([Q])),
    },
    "two-questions": {
        "typesafe-jev": (_jev({
            "addressed": {"type": "noul", "instructions": ADDRESSED_QUESTION,
                          "criteria": {"true": ADDRESSED_TRUE, "false": ADDRESSED_FALSE}},
            "welcome": {"type": "noul", "instructions": WELCOME_QUESTION,
                        "criteria": {"true": WELCOME_TRUE, "false": WELCOME_FALSE}},
        }), _jev_min(["addressed", "welcome"])),
        "openai-decisions": (_decisions([
            _predicate("addressed", f"{ADDRESSED_QUESTION}\n\nAnswer true when: {ADDRESSED_TRUE}"
                                    f"\n\nAnswer false when: {ADDRESSED_FALSE}"),
            _predicate("welcome", f"{WELCOME_QUESTION}\n\nAnswer true when: {WELCOME_TRUE}"
                                  f"\n\nAnswer false when: {WELCOME_FALSE}"),
        ]), _decisions_min(["addressed", "welcome"])),
    },
    "few-shot": {
        "typesafe-jev": (_jev({Q: {"type": "noul", "instructions": f"{wc.INSTRUCTIONS}\n\n{FEW_SHOT}",
                                   "criteria": {"true": wc.TRUE_CRITERIA, "false": wc.FALSE_CRITERIA}}}),
                         _jev_min([Q])),
        "openai-decisions": (_decisions([_predicate(Q, f"{wc.DECISIONS_INSTRUCTIONS}\n\n{FEW_SHOT}")]), _decisions_min([Q])),
    },
    "jev-bullets": {
        "typesafe-jev": (_jev({Q: {"type": "noul", "instructions": wc.INSTRUCTIONS,
                                   "criteria": {"true": BULLET_TRUE, "false": BULLET_FALSE}}}),
                         _jev_min([Q])),
    },
}


def get(variant: str, provider: str) -> tuple[Callable, Callable]:
    try:
        return VARIANTS[variant][provider]
    except KeyError:
        raise ValueError(f"No {variant!r} variant for {provider}") from None


def preview(variant: str, provider: str) -> str:
    """The fixed part of a variant's request, for review; no message data."""
    build, _ = get(variant, provider)
    return json.dumps(build({"assistant": {"name": "Tag"}, "current_message": "", "thread_history": []}, "model"),
                      indent=1)
