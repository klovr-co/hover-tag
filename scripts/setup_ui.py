"""Questionary setup prompts with a numbered fallback for plain terminals."""
from __future__ import annotations

import os
import shutil
import sys
import textwrap

try:
    import tag_display as display
except ImportError:
    from scripts import tag_display as display


class Paused(Exception):
    """The operator chose to keep progress and leave setup."""


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


def _instructions(*, multiple: bool = False) -> str:
    actions = ["↑/↓ Move", *(["Space Toggle"] if multiple else []),
               "Enter Continue" if multiple else "Enter Select", "q Exit"]
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


def choose(title: str, options: list[str], *, default: int = 0) -> int:
    print()
    if not keyboard_available():
        print(f"  {title}\n")
        for index, label in enumerate(options, 1):
            print(f"  {index}. {label}")
        while True:
            answer = input(f"Choice [{default + 1}] (q to save and exit): ").strip()
            if answer.lower() == "q":
                raise Paused()
            if not answer:
                print()
                return default
            if answer.isascii() and answer.isdigit() and 1 <= int(answer) <= len(options):
                print()
                return int(answer) - 1
            message("Choose a displayed number.")
    import questionary

    choices = [questionary.Choice(label, value=index) for index, label in enumerate(options)]
    return _ask(questionary.select(
        title,
        choices=choices,
        default=choices[default],
        instruction=_instructions(),
        **_prompt_options(single=True),
    ))


def checklist(labels: list[str], selected: set[int]) -> set[int]:
    selected = set(selected)
    print()
    if not keyboard_available():
        print("  Choose channels")
        while True:
            for index, label in enumerate(labels):
                print(f"  {index + 1}. [{'x' if index in selected else ' '}] {label}")
            answer = input("Toggle numbers (1,3), Enter to continue, q to save and exit: ").strip()
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
        instruction=_instructions(multiple=True),
        validate=lambda values: bool(values) or "Select at least one channel.",
        **_prompt_options(),
    )))
