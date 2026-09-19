"""Small, noninteractive terminal summary; no service or configuration writes."""
import os
import sys
import shutil
import subprocess
import textwrap

try:
    from tag_mascot import PALETTE, PIXELS
except ImportError:
    from scripts.tag_mascot import PALETTE, PIXELS


ACCENT = "38;2;56;207;241"
MUTED = "90"
WARNING = "33"
SUCCESS = "38;2;149;197;112"


def color_available():
    return sys.stdout.isatty() and os.getenv("TERM", "") not in {"", "dumb"} and "NO_COLOR" not in os.environ


def mascot_banner():
    """A smiling pixel droplet inspired by Tag's supplied mascot reference.

    Keep decorative background colors inside the banner, and leave plain output
    free of the illustration. This never changes the user's terminal palette.
    """
    width = content_width()
    if not color_available():
        return False
    ice, navy = "#bfe6fd", "#083778"
    sprite_width, sprite_height = 13, 14
    height = sprite_height // 2 + 2
    grid = [[(" ", navy, ice) for _ in range(width)] for _ in range(height)]
    title_row = height // 2 - 2

    def put(row, column, text, fg=navy, bg=ice):
        for offset, char in enumerate(text):
            if 0 <= column + offset < width:
                grid[row][column + offset] = (char, fg, bg)

    if width >= 60:
        for row, text in enumerate(("▀█▀  ▄▀█  █▀▀", " █   █▀█  █▄█"), title_row):
            put(row, 3, text)
        put(title_row + 3, 3, "Your Slack assistant")
    else:
        put(title_row, 3, "tag")
        put(title_row + 3, 3, "Slack assistant")

    # Scale the measured source grid, keeping the full map as the source of truth.
    def pixel(x, y):
        source_x = min(27, int((x + .5) * 28 / sprite_width))
        source_y = min(33, int((y + .5) * 34 / sprite_height))
        return PALETTE[PIXELS[source_y][source_x]]

    if width >= 40:
        for row in range(sprite_height // 2):
            for column in range(sprite_width):
                put(row + 1, width - sprite_width - 3 + column, "▀",
                    pixel(column, row * 2), pixel(column, row * 2 + 1))

    def rgb(value):
        return ";".join(str(int(value[index:index + 2], 16)) for index in (1, 3, 5))

    for row in grid:
        chunks, previous = [], None
        for char, fg, bg in row:
            if (fg, bg) != previous:
                chunks.append(f"\033[38;2;{rgb(fg)};48;2;{rgb(bg)}m")
                previous = fg, bg
            chunks.append(char)
        print("  " + "".join(chunks) + "\033[0m")
    return True


def content_width():
    return max(12, min(72, shutil.get_terminal_size((80, 24)).columns - 4))


def paragraph(text, code="", *, indent="  "):
    """Wrap before styling so ANSI sequences never count toward line width."""
    for line in textwrap.wrap(text, width=max(8, content_width() - len(indent) + 2),
                              break_long_words=True, break_on_hyphens=False):
        print(indent + styled(line, code) if code else indent + line)


def rule():
    print("  " + styled("─" * content_width(), MUTED))


def header(section, detail=""):
    print()
    if mascot_banner():
        print()
        paragraph(section.upper(), "1;" + ACCENT)
    else:
        print("  " + styled("tag", "1;" + ACCENT) + "  /  " + styled(section, MUTED))
    rule()
    if detail:
        paragraph(detail, MUTED)
        print()


def status_row(name, value, good):
    marker = "●" if good else "!"
    code = SUCCESS if good else WARNING
    paragraph(f"{marker}  {name:<9} {value}", code, indent="    ")


def backend_status(backend="codex", *, search_path=None):
    if backend not in {"codex", "claude"}:
        return "Unknown agent", False
    executable = shutil.which(backend, path=search_path)
    if not executable:
        return "Not installed", False
    if backend == "claude":
        return "Experimental · sign-in not checked", False
    try:
        result = subprocess.run([executable, "login", "status"], capture_output=True,
                                text=True, timeout=3, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return "Sign-in check unavailable", False
    if result.returncode == 0:
        return "Signed in · task not tested", True
    return "Sign-in unverified · run codex login status", False


def styled(text, code):
    return f"\033[{code}m{text}\033[0m" if color_available() else text


def summary(state, command, *, slack=None, memory=None, backend=None, agent=None):
    agent = agent if agent is not None else backend_status(backend) if backend else None
    if agent and not agent[1] and state in {"ready", "running"}:
        state, command = "needs_attention", "tag doctor"
    header("Overview")
    label = state.replace("_", " ").capitalize()
    good = state in {"ready", "running"}
    attention = state in {"needs_attention", "invalid_configuration", "setup_incomplete"}
    marker = "●" if good else "!" if attention else "○"
    print()
    paragraph(f"{marker}  {label}", "1;" + (SUCCESS if good else WARNING if attention else MUTED))
    descriptions = {
        "not_configured": "Connect Slack, choose your channels, and bring your agent online.",
        "setup_incomplete": "Pick up where you left off. Your completed setup steps are saved.",
        "stopped": "Your configuration is saved. Start Tag when you’re ready.",
        "needs_attention": "Check the items below before asking Tag to take on a task.",
    }
    if state in descriptions:
        paragraph(descriptions[state], MUTED)
    print()
    if agent or slack is not None or memory is not None:
        paragraph("CONNECTIONS", MUTED)
    if agent:
        name = {"codex": "Codex", "claude": "Claude"}.get(backend, "Agent")
        status_row(name, agent[0], agent[1])
    for name, value, good, bad in (
        ("Slack", slack, "Connected", "Not connected or unverified"),
        ("Memory", memory, "Ready", "Not ready"),
    ):
        if value is not None:
            status_row(name, good if value else bad, value)
    print()
    prompt = {"tag setup": "Get started" if state == "not_configured" else "Continue setup",
              "tag start": "Start Tag", "tag doctor": "Check what needs attention",
              "tag status": "View status"}.get(command, "Next step")
    rule()
    paragraph(prompt, MUTED)
    paragraph(f"› {command}", "1;" + ACCENT)
    print()
    paragraph("tag settings   ·   tag --help", MUTED)
    print()
