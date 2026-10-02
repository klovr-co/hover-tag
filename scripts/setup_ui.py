"""Questionary setup prompts with a numbered fallback for plain terminals.

Setting ``TAG_SETUP_PROTOCOL=jsonl`` turns every prompt into a JSON-lines
exchange so a graphical client can drive the same setup flow: questions,
progress messages and the final result are written to stdout one JSON object
per line, and each answer is read from stdin as ``{"answer": ...}``. Clients
should ignore stdout lines that are not JSON objects.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import textwrap

try:
    import tag_display as display
except ImportError:
    from scripts import tag_display as display


class Paused(Exception):
    """The operator chose to keep progress and leave setup."""


SAVE_AND_EXIT = "Exit · finish setup later"
PROTOCOL_ENV = "TAG_SETUP_PROTOCOL"
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def protocol_active() -> bool:
    return os.getenv(PROTOCOL_ENV) == "jsonl"


class _MessageStream:
    """Turn ordinary setup prose into protocol message events, line by line."""

    def __init__(self, raw):
        self.raw = raw
        self.pending = ""

    def write(self, text: str) -> int:
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            line = _ANSI.sub("", line).strip()
            if line:
                _emit_raw(self.raw, {"type": "message", "text": line})
        return len(text)

    def flush(self) -> None:
        self.raw.flush()

    def isatty(self) -> bool:
        return False


def _emit_raw(raw, event: dict) -> None:
    raw.write(json.dumps(event, ensure_ascii=False) + "\n")
    raw.flush()


def enter_protocol() -> None:
    """Route prose through message events when a client drives setup. Idempotent."""
    if protocol_active() and not isinstance(sys.stdout, _MessageStream):
        sys.stdout = _MessageStream(sys.__stdout__)


def emit(event: dict) -> None:
    raw = sys.stdout.raw if isinstance(sys.stdout, _MessageStream) else sys.__stdout__
    _emit_raw(raw, event)


def question_id(prompt: str) -> str:
    """Fallback identifier for prompts without an explicit ``qid``."""
    return re.sub(r"[^a-z0-9]+", "_", prompt.casefold()).strip("_") or "question"


class GoBack(Exception):
    """The client asked to return to the previous question.

    Setup restarts from the top, replays ``replay`` without showing it, and
    asks ``target`` again with its earlier answer as the default.
    """

    def __init__(self, replay: list[tuple[str, object]], target: tuple[str, object]):
        super().__init__(target[0])
        self.replay, self.target = replay, target


# Answers given since the last step that can't be undone (see commit()).
_history: list[tuple[str, object]] = []
_replay: list[tuple[str, object]] = []
_target: tuple[str, object] | None = None


def commit() -> None:
    """Mark a point of no return, such as a Slack-side change: Back stops here."""
    _history.clear()


def going_back_to(qid: str) -> bool:
    """Whether Back is returning to this question, so it must be asked even if already answered."""
    return bool(_target and _target[0] == qid)


def start_replay(replay: list[tuple[str, object]], target: tuple[str, object]) -> None:
    global _target
    _history.clear()
    _replay[:] = replay
    _target = target


def _previous(kind: str, answer: object, details: dict) -> dict:
    """Offer the earlier answer as the default when a question is asked again."""
    options = details.get("options") or []
    if kind == "choose":
        index = options.index(answer) if isinstance(answer, str) and answer in options else answer
        return {"default": index} if isinstance(index, int) and not isinstance(index, bool) else {}
    if kind == "multi" and isinstance(answer, list):
        return {"selected": sorted(options.index(item) if isinstance(item, str) and item in options else item
                                   for item in answer if isinstance(item, (int, str)))}
    if kind in {"text", "confirm"}:
        return {"default": answer}
    return {}  # Secrets are never sent back to the client.


def ask_client(kind: str, prompt: str, *, qid: str | None = None, **details):
    """Send one question and block for its answer. EOF pauses setup.

    ``id`` is a stable name for the question, so clients can match answers
    without parsing prompt wording, which may change between releases.
    ``can_go_back`` says whether ``{"back": true}`` may be sent instead of an
    answer; it is false for the first question and after Slack-side changes.
    """
    global _target
    qid = qid or question_id(prompt)
    if _replay:
        if _replay[0][0] == qid:
            answer = _replay.pop(0)[1]
            _history.append((qid, answer))
            return answer
        _replay.clear()  # The flow changed; ask normally from here.
    if _target and _target[0] == qid:
        details = {**details, **_previous(kind, _target[1], details)}
    _target = None
    while True:
        emit({"type": "question", "id": qid, "kind": kind, "prompt": prompt, **details,
              "can_go_back": bool(_history)})
        line = sys.stdin.readline()
        if not line:
            raise Paused()
        try:
            reply = json.loads(line)
        except ValueError:
            raise RuntimeError("The setup client sent an unreadable answer") from None
        if isinstance(reply, dict) and reply.get("back"):
            if not _history:
                message("There is nothing to go back to here.")
                continue
            target = _history.pop()
            raise GoBack(list(_history), target)
        if not isinstance(reply, dict) or "answer" not in reply:
            raise RuntimeError("The setup client sent an answer without a value")
        if reply.get("pause"):
            raise Paused()
        _history.append((qid, reply["answer"]))
        return reply["answer"]


def _option_index(answer, labels: list[str]) -> int:
    if isinstance(answer, int) and not isinstance(answer, bool) and 0 <= answer < len(labels):
        return answer
    if isinstance(answer, str) and answer in labels:
        return labels.index(answer)
    raise RuntimeError("The setup client chose an option that was not offered")


def text(prompt: str, default: str | None = None, *, secret: bool = False, qid: str | None = None) -> str:
    """Protocol counterpart of input()/getpass for free-form answers."""
    answer = ask_client("secret" if secret else "text", prompt, qid=qid,
                        **({} if secret else {"default": default or ""}))
    if not isinstance(answer, str):
        raise RuntimeError("The setup client sent a non-text answer")
    return answer.strip() or ("" if secret else (default or ""))


def confirm(prompt: str, default: bool, *, qid: str | None = None) -> bool:
    answer = ask_client("confirm", prompt, qid=qid, default=default)
    if not isinstance(answer, bool):
        raise RuntimeError("The setup client sent a non-boolean confirmation")
    return answer


def _setup_label(label: str) -> str:
    """Make setup-only exit actions explicit without changing option values."""
    return SAVE_AND_EXIT if label == "Save and exit" else label


def message(text: str, *, code: str = "", indent: str = "  ") -> None:
    """Print setup prose in the same gutter as the header and prompts."""
    # ``display.paragraph`` deliberately owns wrapping, while this helper owns
    # the gutter for every line of a multi-line status returned by a command.
    # Without splitting first, command output could resume at column zero.
    for line in str(text).splitlines() or [""]:
        if line:
            display.paragraph(line, code, indent=indent)
        else:
            print()


def notice(title: str, body: str, *, code: str = "", footer: str = "") -> None:
    """Keep terminal prose readable without relying on color or hard wrapping."""
    width = max(12, min(72, shutil.get_terminal_size((80, 24)).columns - 4))

    def paragraph(text):
        for line in textwrap.wrap(text, width=width, break_long_words=False, break_on_hyphens=False):
            print(f"  {line}")

    print()
    paragraph(title)
    print()
    for index, part in enumerate(body.split("\n")):
        if index:
            print()
        paragraph(part)
    if code:
        print()
        paragraph(f"Code: {code}")
    if footer:
        print()
        paragraph(footer)


def screen(step: int, title: str, detail: str = "", *, target: str = "") -> None:
    display.header(
        "Setup",
        target or display.target_detail(os.getenv("TAG_ID", "default")),
    )
    stages = ("Connect Slack", "App", "Channels", "Finish")
    print()
    if display.content_width() < 60:
        display.paragraph(f"STEP {step}/4 · {stages[step - 1]}", "1;" + display.ACCENT)
    else:
        pieces = []
        for index, label in enumerate(stages, 1):
            marker = "✓" if index < step else str(index)
            code = "1;" + display.ACCENT if index == step else display.MUTED
            pieces.append(display.styled(f"{marker} {label}", code))
        print("  " + "   /   ".join(pieces))
    print()
    display.paragraph(title, "1")
    if detail:
        display.paragraph(detail, display.MUTED)


def keyboard_available() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty() and os.getenv("TERM") != "dumb"


def _instructions(*, multiple: bool = False, setup_incomplete: bool = False) -> str:
    actions = ["↑/↓ Move", *(["Space Toggle"] if multiple else []),
               "Enter Continue" if multiple else "Enter Select",
               ("q Exit · finish setup later"
                if setup_incomplete else "q Exit")]
    width = max(12, shutil.get_terminal_size((80, 24)).columns - 8)
    lines, line = [], ""
    for action in actions:
        candidate = f"{line}   {action}" if line else action
        if len(candidate) > width and line:
            lines.append(line)
            line = action
        else:
            line = candidate
    lines.append(line)
    return "\n" + "\n".join("    " + line for line in lines) + "\n"


def _prompt_options(*, single: bool = False) -> dict:
    import questionary
    from prompt_toolkit.output import ColorDepth

    options = dict(
        qmark=" ",
        # Questionary writes a space before and after the pointer. The leading
        # space here aligns its arrow to Tag's two-space content gutter; choice
        # labels then sit one nested level in at column four.
        pointer=" ›",
        style=questionary.Style([
            ("question", "bold"),
            ("answer", "fg:#38cff1"),
            ("pointer", "fg:#38cff1 bold"),
            ("highlighted", "fg:#38cff1 bold"),
            # A select prompt retains its initial default as "selected" even
            # after the pointer moves. Don't show it as a second active answer.
            ("selected", "fg:default bg:default noreverse nobold" if single else "fg:#38cff1 bg:default noreverse nobold"),
            ("instruction", "fg:#89949f"),
            ("text", ""),
            ("checkbox", "fg:#89949f"),
            ("checked", "fg:#38cff1"),
        ]),
    )
    if "NO_COLOR" in os.environ:
        options["color_depth"] = ColorDepth.DEPTH_1_BIT
    return options


def _ask(question):
    # Keep Tag's pause action while letting Questionary own terminal restoration.
    @question.application.key_bindings.add("q", eager=True)
    @question.application.key_bindings.add("c-d", eager=True)
    def pause(event):
        event.app.exit(result=None)

    try:
        answer = question.unsafe_ask()
    except (KeyboardInterrupt, EOFError):
        raise Paused() from None
    finally:
        print()
    if answer is None:
        raise Paused()
    return answer


def choose(title: str, options: list[str], *, default: int = 0, qid: str | None = None) -> int:
    setup_incomplete = "Save and exit" in options
    labels = [_setup_label(label) for label in options]
    if protocol_active():
        index = _option_index(ask_client("choose", title, qid=qid, options=labels, default=default), labels)
        if options[index] == "Save and exit":
            raise Paused()
        return index
    print()
    if not keyboard_available():
        print(f"  {title}\n")
        for index, label in enumerate(labels, 1):
            print(f"  {index}. {label}")
        while True:
            exit_hint = (
                "q to exit and finish setup later"
                if setup_incomplete else "q to exit"
            )
            answer = input(f"Choice [{default + 1}] ({exit_hint}): ").strip()
            if answer.lower() == "q":
                raise Paused()
            if not answer:
                print()
                return default
            if answer.isascii() and answer.isdigit() and 1 <= int(answer) <= len(labels):
                print()
                return int(answer) - 1
            message("Choose a displayed number.")
    import questionary

    choices = [questionary.Choice(label, value=index) for index, label in enumerate(labels)]
    return _ask(questionary.select(
        title,
        choices=choices,
        default=choices[default],
        instruction=_instructions(setup_incomplete=setup_incomplete),
        **_prompt_options(single=True),
    ))


def checklist(labels: list[str], selected: set[int], *, qid: str = "channels") -> set[int]:
    selected = set(selected)
    if protocol_active():
        answer = ask_client("multi", "Choose channels", qid=qid, options=labels, selected=sorted(selected))
        if not isinstance(answer, list) or not answer:
            raise RuntimeError("The setup client must choose at least one channel")
        return {_option_index(item, labels) for item in answer}
    print()
    if not keyboard_available():
        print("  Choose channels")
        while True:
            for index, label in enumerate(labels):
                print(f"  {index + 1}. [{'x' if index in selected else ' '}] {label}")
            answer = input(
                "Toggle numbers (1,3), Enter to continue, q to exit and finish setup later: "
            ).strip()
            if answer.lower() == "q":
                raise Paused()
            if not answer:
                if selected:
                    return selected
                message("Select at least one channel.")
                continue
            pieces = answer.split(",")
            if all(piece.strip().isascii() and piece.strip().isdigit() and 1 <= int(piece) <= len(labels) for piece in pieces):
                selected.symmetric_difference_update({int(piece) - 1 for piece in pieces})
            else:
                message("Choose displayed channel numbers.")
    import questionary

    choices = [
        questionary.Choice(label, value=index, checked=index in selected)
        for index, label in enumerate(labels)
    ]
    return set(_ask(questionary.checkbox(
        "Choose channels",
        choices=choices,
        instruction=_instructions(multiple=True, setup_incomplete=True),
        validate=lambda values: bool(values) or "Select at least one channel.",
        **_prompt_options(),
    )))
