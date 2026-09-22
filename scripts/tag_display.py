"""Small, noninteractive terminal summary; no service or configuration writes."""
import os
import sys
import shutil
import subprocess
import textwrap
from pathlib import Path

try:
    from tag_mascot import PALETTE, PIXELS
except ImportError:
    from scripts.tag_mascot import PALETTE, PIXELS


ACCENT = "38;2;56;207;241"
MUTED = "90"
WARNING = "33"
SUCCESS = "38;2;149;197;112"
BRAND_NAME = "@Tag by Hover"
BRAND_URL = "https://hover.team/tag"
ASCII_FALLBACK = str.maketrans({
    "✓": "+",
    "●": "*",
    "○": "o",
    "◌": "o",
    "›": ">",
    "─": "-",
    "·": ".",
    "…": "...",
    "–": "-",
    "—": "-",
    "’": "'",
    "▀": "#",
    "█": "#",
    "▄": "#",
})


def terminal_text(text):
    """Return text the active stdout encoding can write without failing."""
    encoding = getattr(sys.stdout, "encoding", None)
    if not encoding:
        return text
    try:
        text.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return text.translate(ASCII_FALLBACK)
    return text


def emit(text=""):
    print(terminal_text(str(text)))


def color_available():
    return sys.stdout.isatty() and os.getenv("TERM", "") not in {"", "dumb"} and "NO_COLOR" not in os.environ


def mascot_banner():
    """A smiling pixel droplet inspired by Tag's supplied mascot reference.

    Keep decorative background colors inside the banner, and leave plain output
    free of the illustration. This never changes the user's terminal palette.
    """
    width = content_width()
    brand_width = 3 + max(len(BRAND_NAME), len(BRAND_URL))
    if not color_available() or width < brand_width:
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

    put(title_row, 3, BRAND_NAME)
    put(title_row + 2, 3, BRAND_URL)

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
        emit("  " + "".join(chunks) + "\033[0m")
    return True


def content_width():
    return max(12, min(72, shutil.get_terminal_size((80, 24)).columns - 4))


def paragraph(text, code="", *, indent="  "):
    """Wrap before styling so ANSI sequences never count toward line width."""
    for line in textwrap.wrap(text, width=max(8, content_width() - len(indent) + 2),
                              break_long_words=True, break_on_hyphens=False):
        emit(indent + styled(line, code) if code else indent + line)


def short_path(value):
    """Use a stable home-relative path when it is easier to scan."""
    path = str(value)
    home = str(Path.home())
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def target_detail(
    tag_id="default", team_id="", app_id="", app_name="", *, suffix=""
):
    """Format one consistent, user-facing command target description."""
    parts = [f"Tag '{tag_id}'"]
    parts.append(
        f"Slack workspace {team_id}" if team_id else "Slack workspace not configured"
    )
    if app_id:
        label = f"{app_name} ({app_id})" if app_name else app_id
        parts.append(f"App {label}")
    if suffix:
        parts.append(suffix)
    return " · ".join(parts)


def rule():
    emit("  " + styled("─" * content_width(), MUTED))


def header(section, detail=""):
    emit()
    if mascot_banner():
        emit()
        paragraph(section.upper(), "1;" + ACCENT)
    else:
        paragraph(f"{BRAND_NAME}  /  {section}", "1;" + ACCENT)
        paragraph(BRAND_URL, MUTED)
    rule()
    if detail:
        paragraph(detail, MUTED)


def status_row(name, value, good):
    marker = "●" if good else "!"
    code = SUCCESS if good else WARNING
    paragraph(f"{marker}  {name:<14} {value}", code, indent="    ")


def section(label):
    emit()
    paragraph(label.upper(), MUTED)


def info_row(name, value, *, good=None):
    """Render one aligned row for lifecycle and informational screens."""
    if good is None:
        if len(f"{name:<12} {value}") > content_width() - 4:
            paragraph(name, MUTED, indent="    ")
            paragraph(value, indent="      ")
            return
        paragraph(f"{name:<12} {value}", indent="    ")
        return
    marker = "✓" if good else "!"
    code = SUCCESS if good else WARNING
    paragraph(f"{marker}  {name:<14} {value}", code, indent="    ")


def pending_row(name, value):
    """Render a readiness step before its blocking check has completed."""
    paragraph(f"◌  {name:<14} {value}", MUTED, indent="    ")


def next_action(label, command, *, detail=""):
    emit()
    rule()
    paragraph(label, MUTED)
    paragraph(f"› {command}", "1;" + ACCENT)
    if detail:
        paragraph(detail, MUTED)
    emit()


def completion(title, detail="", *, next_label="", next_command=""):
    emit()
    rule()
    paragraph(f"✓  {title}", "1;" + SUCCESS)
    if detail:
        paragraph(detail, MUTED)
    if next_command:
        emit()
        paragraph(next_label or "Next step", MUTED)
        paragraph(f"› {next_command}", "1;" + ACCENT)
    emit()


def failure(title, detail, *, next_command=""):
    header(title)
    emit()
    paragraph("!  Needs attention", "1;" + WARNING)
    paragraph(detail, MUTED)
    if next_command:
        next_action("Recommended next step", next_command)
    else:
        emit()


def doctor_summary(report, *, title="Doctor"):
    """Collapse low-level probes into the product concepts operators recognize."""
    checks = report.get("checks", [])

    def matching(predicate):
        return [item for item in checks if predicate(str(item.get("check", "")))]

    groups = [
        ("Runtime", matching(lambda label: label in {
            "Tag runtime dependencies", "Tag command", "installer", "release metadata"
        })),
        ("Configuration", matching(lambda label: label.startswith(("SLACK_", "MFS_TOKEN")) or label in {
            "supported transport", "agent workspace", "Slack app manifest", "Slack allowed users"
        })),
        ("Memory", matching(lambda label: label.startswith("MFS") and not label.startswith("MFS_TOKEN"))),
        ("Agent", matching(lambda label: label.startswith("backend") or label in {"OPENTAG_BACKEND", "supported backend"})),
        ("Slack", matching(lambda label: label.startswith("Slack") and label not in {"Slack app manifest", "Slack allowed users"})),
    ]
    header(title, "Verifying the runtime, connections, and permissions Tag needs.")
    section("Readiness")
    for name, items in groups:
        if not items:
            continue
        good = all(bool(item.get("ok")) for item in items)
        if name == "Slack":
            channels = sum(
                1
                for item in items
                if str(item.get("check", "")).startswith("Slack channel ")
                and not str(item.get("check", "")).startswith("Slack channel history")
            )
            value = f"{channels} channel{'s' if channels != 1 else ''} accessible" if good else "Connection or channel access failed"
        elif name == "Memory":
            if report.get("offline"):
                value = "Configuration valid" if good else "Configuration needs attention"
            else:
                value = "Healthy and scopes accessible" if good else "Server or scope access failed"
        else:
            value = "Ready" if good else "Needs attention"
        info_row(name, value, good=good)

    failed = [item for item in checks if not item.get("ok")]
    if failed:
        section("Attention")
        for item in failed[:5]:
            info_row(str(item.get("check", "Check")), "Failed", good=False)
        action = failed[0].get("next_action") or "Run tag inspect --json"
        next_action("Recommended next step", str(action))
    else:
        completion("All checks passed", "Tag is ready to start or continue running.")


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
    emit()
    paragraph(f"{marker}  {label}", "1;" + (SUCCESS if good else WARNING if attention else MUTED))
    descriptions = {
        "not_configured": "Connect Slack, choose your channels, and bring your agent online.",
        "setup_incomplete": "Pick up where you left off. Your completed setup steps are saved.",
        "stopped": "Your configuration is saved. Start Tag when you’re ready.",
        "needs_attention": "Check the items below before asking Tag to take on a task.",
    }
    if state in descriptions:
        paragraph(descriptions[state], MUTED)
    emit()
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
    emit()
    prompt = {"tag setup": "Get started" if state == "not_configured" else "Continue setup",
              "tag start": "Start Tag", "tag doctor": "Check what needs attention",
              "tag status": "View status"}.get(command, "Next step")
    rule()
    paragraph(prompt, MUTED)
    paragraph(f"› {command}", "1;" + ACCENT)
    emit()
    paragraph("tag settings   ·   tag --help", MUTED)
    emit()
