"""Each backend's own permission settings for a Tag, and the words the apps show for them.

Codex and Claude control access differently, so each keeps its native settings:
Codex a sandbox, when it asks, and who answers; Claude a permission mode. The
bridge stays backend-neutral; only the backend adapters read their own here.
"""

from __future__ import annotations

import ast
import fnmatch
import json
import os
import re
import shlex
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:
    from .tag_paths import tag_home
except ImportError:
    from tag_paths import tag_home


Option = tuple[str, str, str]  # value, label, what it means

CODEX_SANDBOX: tuple[Option, ...] = (
    ("read-only", "Read only", "Reads files and runs commands that don't change anything."),
    ("workspace-write", "Workspace write", "Changes files only in its folder."),
    ("danger-full-access", "Full access", "No sandbox: changes anything on this computer as you."),
)
CODEX_APPROVALS: tuple[Option, ...] = (
    ("", "Default", "Uses approval_policy from your own Codex config: on request unless you changed it."),
    ("untrusted", "Untrusted", "Asks before commands that aren't known to be safe."),
    ("on-request", "On request", "Codex decides when to ask, mostly to go beyond the sandbox."),
    ("never", "Never", "Never asks. Anything the sandbox blocks fails."),
)
CODEX_REVIEWER: tuple[Option, ...] = (
    ("auto_review", "Auto review", "Codex's reviewer decides. You can retry what it denies."),
    ("user", "User", "Each request comes to you privately in Slack."),
)
CLAUDE_MODE: tuple[Option, ...] = (
    ("default", "Default", "Asks before file changes and commands."),
    ("acceptEdits", "Accept edits", "Changes files in its folder. Asks before commands."),
    ("auto", "Auto", "Checks riskier actions itself and asks you when unsure."),
    ("dontAsk", "Don't ask", "Never asks. Does only what's already allowed and refuses the rest."),
    ("bypassPermissions", "Bypass permissions", "Skips approvals. Only deny rules and hooks can stop it."),
)
CLAUDE_CHROME: tuple[Option, ...] = (
    ("0", "Off", "Turns off Claude in Chrome. Browser tools from the Tag's MCP servers still work."),
    ("1", "On", "Uses this computer's signed-in Chrome. Anyone who can ask the Tag can ask it to act there."),
)
# Setting, its options, and the default when unset. Unset Codex approvals send
# nothing, so Codex follows the operator's own configuration.
SETTINGS: dict[str, tuple[str, tuple[Option, ...], str]] = {
    "codex-sandbox": ("OPENTAG_CODEX_SANDBOX", CODEX_SANDBOX, "workspace-write"),
    "codex-approvals": ("OPENTAG_CODEX_APPROVALS", CODEX_APPROVALS, ""),
    "codex-reviewer": ("OPENTAG_CODEX_REVIEWER", CODEX_REVIEWER, "auto_review"),
    "claude-mode": ("OPENTAG_CLAUDE_PERMISSION_MODE", CLAUDE_MODE, "auto"),
    "claude-chrome": ("OPENTAG_CLAUDE_CHROME", CLAUDE_CHROME, "0"),
}
KEYS = {key: name for name, (key, _, _) in SETTINGS.items()}
# The rollback transports have fixed flags, so they run only with the settings they match:
# claude -p always skips permission checks, which is Bypass permissions.
LEGACY_CLAUDE_MODES = frozenset({"bypassPermissions"})


def validation_error(key: str, value: str) -> str | None:
    """Why ``value`` can't be saved for this setting, or None; empty means the default."""
    _, options, _ = SETTINGS[KEYS[key]]
    allowed = [option[0] for option in options]
    if value and value not in allowed:
        return "Choose " + ", ".join(allowed)
    return None


def _value(name: str, values: dict[str, str] | None) -> str:
    """A saved setting or its default; an unknown value fails instead of loosening access."""
    key, options, default = SETTINGS[name]
    raw = ((values if values is not None else os.environ).get(key) or "").strip()
    if raw and raw not in {option[0] for option in options}:
        raise ValueError(f"{key} must be one of " + ", ".join(option[0] for option in options))
    return raw or default


def claude_mode(values: dict[str, str] | None = None) -> str:
    """The Agent SDK permission mode for this Tag."""
    return _value("claude-mode", values)


def claude_chrome(values: dict[str, str] | None = None) -> bool:
    """Whether Agent SDK runs connect to the operator's Chrome through Claude in Chrome."""
    return _value("claude-chrome", values) == "1"


def codex_thread(values: dict[str, str] | None = None) -> dict[str, str]:
    """The sandbox and approval settings for App Server ``thread/start`` and ``thread/resume``."""
    params = {"sandbox": _value("codex-sandbox", values), "approvalsReviewer": _value("codex-reviewer", values)}
    if approvals := _value("codex-approvals", values):
        params["approvalPolicy"] = approvals
    if params.get("approvalPolicy") == "never":
        params["approvalsReviewer"] = "user"  # Nothing is asked, so nothing is reviewed.
    return params


def check_legacy_transport(backend: str, values: dict[str, str] | None = None, workdir: Path | None = None) -> None:
    """Refuse settings the rollback transport can't enforce, rather than run with more access."""
    source = values if values is not None else os.environ
    if backend == "claude":
        if claude_mode(values) not in LEGACY_CLAUDE_MODES:
            raise ValueError("OPENTAG_CLAUDE_TRANSPORT=print always skips Claude's permission checks; "
                             "set the Claude permission mode to Bypass permissions or use OPENTAG_CLAUDE_TRANSPORT=sdk.")
    elif any((source.get(SETTINGS[name][0]) or "").strip() for name in ("codex-sandbox", "codex-approvals", "codex-reviewer")):
        raise ValueError("Codex permission settings need OPENTAG_CODEX_TRANSPORT=app-server.")
    elif workdir is not None and codex_tag_restricts(workdir):
        # codex exec approves for you and can't confirm it read the Tag's rules.
        raise ValueError("This Tag's Codex rules that block or ask need OPENTAG_CODEX_TRANSPORT=app-server.")


# ---- Rules: this Tag's, all Tags', and your own -------------------------------------
#
# Both agents read rules from several files and add them up; a block anywhere
# wins over an allow anywhere. Three layers apply to each Tag:
#   tag       the Tag's own, in its folder; Tag.app and Always allow in Slack edit them
#   all_tags  Tag's shared rules for every Tag; Settings → AI connections edits them
#   personal  your own Codex and Claude Code rules; Tag only reads them, because they
#             also apply when you use Codex or Claude Code yourself

Rule = dict[str, str]  # kind (rule, folder, command, host), value, decision (allow, ask, block)
SCOPES = ("tag", "folder", "all_tags", "personal")
CLAUDE_DECISIONS = (("allow", "allow"), ("ask", "ask"), ("deny", "block"))
CODEX_DECISIONS = {"allow": "allow", "prompt": "ask", "forbidden": "block", "deny": "block"}
# Claude's own tools are capitalized (Bash, WebFetch); MCP tools start with mcp__.
# One rule only: `Bash(a) Bash(b)` isn't one, so the parentheses hold no others.
CLAUDE_RULE = re.compile(r"(?:mcp__[A-Za-z0-9_-]+(?:__[A-Za-z0-9_*-]+)?|[A-Z][A-Za-z0-9_-]*)(?:\([^()]+\))?")
CODEX_PROTOCOLS = {"http": "http", "https": "https", "socks5Tcp": "socks5_tcp", "socks5Udp": "socks5_udp"}
CODEX_ALL_TAGS_COPY = "all-tags.rules"


def claude_tag_file(workdir: Path) -> Path:
    """This Tag's Claude rules; Always allow in Slack saves here too."""
    return workdir / ".claude" / "settings.local.json"


def codex_tag_file(workdir: Path) -> Path:
    """This Tag's Codex rules. Codex reads them because Tag trusts the Tag's folder
    (`tag_paths.codex_trust_args`)."""
    return workdir / ".codex" / "rules" / "tag.rules"


def claude_all_tags_file() -> Path:
    """Claude rules every Tag follows; the SDK gets them as extra settings."""
    return tag_home() / "shared" / "permissions" / "claude.json"


def codex_all_tags_file() -> Path:
    """Codex rules every Tag follows; each Tag's folder gets a copy Codex reads."""
    return tag_home() / "shared" / "permissions" / "codex.rules"


def claude_personal_file() -> Path:
    """Your own Claude Code settings, which every Tag also follows."""
    return Path(os.getenv("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser() / "settings.json"


def codex_personal_dir() -> Path:
    """Your own Codex rules, which every Tag also follows."""
    return Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))).expanduser() / "rules"


def _read_settings(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(f"Couldn't read {path}") from None
    if not isinstance(data, dict) or not isinstance(data.get("permissions", {}), dict):
        raise ValueError(f"Couldn't read {path}")
    return data


def _write(path: Path, text: str) -> None:
    """Replace a rules file in one step, keeping its permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tag-tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(name, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


@contextmanager
def _locked(path: Path):
    """Hold a short lock on a rules file, so two Slack threads saving at once don't lose a rule.
    The system frees it if Tag dies. The lock file stays: deleting it would let two in."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".tag-lock")
    deadline = time.monotonic() + 5
    with os.fdopen(os.open(lock, os.O_RDWR | os.O_CREAT, 0o600), "r+b") as handle:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise ValueError(f"Another change to {path.name} is in progress; try again") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _claude_rules(path: Path) -> list[Rule] | None:
    try:
        permissions = _read_settings(path).get("permissions", {})
    except ValueError:
        return None
    rules: list[Rule] = []
    for key, decision in CLAUDE_DECISIONS:
        items = permissions.get(key)
        rules += [{"kind": "rule", "value": v, "decision": decision}
                  for v in items if isinstance(v, str)] if isinstance(items, list) else []
    folders = permissions.get("additionalDirectories")
    rules += [{"kind": "folder", "value": v, "decision": "allow"}
              for v in folders if isinstance(v, str)] if isinstance(folders, list) else []
    return rules


UNREAD = "(couldn't read) "


def _unreadable(text: str) -> Rule:
    """A rule Tag can't read. Codex may still enforce it, so it counts as one that asks:
    Tag never assumes it allows more than it does."""
    text = " ".join(text.split())
    return {"kind": "command", "value": UNREAD + (text[:120] + "…" if len(text) > 120 else text), "decision": "ask"}


Pattern = list[list[str]] | str | None  # a command's words (each with its alternatives), a host, or unreadable


def _codex_call(node: ast.AST) -> tuple[Rule, Pattern] | None:
    """One `prefix_rule(...)` or `network_rule(...)` call, as Tag shows it and as it matches.
    Any other call counts as unreadable: it could be one of those under another name."""
    if not isinstance(node, ast.Call):
        return None
    if not (isinstance(node.func, ast.Name) and node.func.id in {"prefix_rule", "network_rule"}):
        return _unreadable(ast.unparse(node)), None
    try:
        args = {k.arg: ast.literal_eval(k.value) for k in node.keywords if k.arg}
    except Exception:  # noqa: BLE001 - any odd literal is just a rule Tag can't read
        args = None
    native = args.get("decision", "allow") if args is not None else None
    decision = CODEX_DECISIONS.get(native) if isinstance(native, str) else None
    if args is None or node.args or decision is None or len(args) < len(node.keywords):
        return _unreadable(ast.unparse(node)), None
    if node.func.id == "network_rule":
        host = args.get("host")
        if not isinstance(host, str) or not host:
            return _unreadable(ast.unparse(node)), None
        return {"kind": "host", "value": host, "decision": decision}, host
    pattern = args.get("pattern")
    words, alternatives = [], []
    for word in pattern if isinstance(pattern, list) and pattern else [None]:
        if isinstance(word, str):
            words.append(shlex.quote(word))
            alternatives.append([word])
        elif isinstance(word, list) and word and all(isinstance(w, str) for w in word):
            words.append("{" + ",".join(word) + "}")  # Codex's alternatives for one word
            alternatives.append(list(word))
        else:
            return _unreadable(ast.unparse(node)), None
    return {"kind": "command", "value": " ".join(words), "decision": decision}, alternatives


def _codex_text_rules(text: str) -> list[tuple[Rule, Pattern]]:
    """Rules in a Codex `.rules` file. Parsed, never run."""
    try:
        nodes = list(ast.walk(ast.parse(text)))
    except Exception:  # noqa: BLE001 - fall back to one line at a time
        nodes = []
        for line in text.splitlines():
            try:
                nodes += list(ast.walk(ast.parse(line.strip())))
            except Exception:  # noqa: BLE001
                if "_rule(" in line:
                    nodes.append(ast.Constant(line))  # Marked unreadable below.
    return [rule for node in nodes
            if (rule := (_unreadable(node.value), None) if isinstance(node, ast.Constant) and isinstance(node.value, str)
                and "_rule(" in node.value else _codex_call(node))]


def _codex_parsed(paths: list[Path]) -> list[tuple[Rule, Pattern]] | None:
    found: list[tuple[Rule, Pattern]] = []
    for path in paths:
        try:
            found += _codex_text_rules(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError):
            return None
    return found


def _codex_rules(paths: list[Path]) -> list[Rule] | None:
    found = _codex_parsed(paths)
    return None if found is None else [rule for rule, _ in found]


def _codex_personal_rules() -> list[Rule] | None:
    try:
        files = sorted(p for p in codex_personal_dir().glob("*.rules") if p.is_file())
    except OSError:
        return None
    return _codex_rules(files)


def _codex_folder_files(workdir: Path) -> list[Path] | None:
    """Every Codex rules file in the Tag's folder; Codex reads them all."""
    try:
        return sorted(p for p in (workdir / ".codex" / "rules").glob("*.rules") if p.is_file())
    except OSError:
        return None


def _scopes(backend: str, workdir: Path | None) -> dict[str, list[Rule] | None]:
    if backend == "codex":
        found = {"all_tags": _codex_rules([codex_all_tags_file()]), "personal": _codex_personal_rules()}
        if workdir is not None:
            others = _codex_folder_files(workdir)
            others = None if others is None else [f for f in others if f.name not in {"tag.rules", CODEX_ALL_TAGS_COPY}]
            found = {"tag": _codex_rules([codex_tag_file(workdir)]),
                     "folder": _codex_rules(others) if others is not None else None, **found}
    else:
        found = {"all_tags": _claude_rules(claude_all_tags_file()), "personal": _claude_rules(claude_personal_file())}
        if workdir is not None:
            found = {"tag": _claude_rules(claude_tag_file(workdir)),
                     "folder": _claude_rules(workdir / ".claude" / "settings.json"), **found}
    return found


def rules(workdir: Path | None = None) -> dict[str, dict[str, list[Rule] | None]]:
    """Each agent's rules by scope: given the Tag's folder, `tag` (the Tag's own) and
    `folder` (other rule files there, which the agent also reads); then `all_tags` and
    `personal`. A list is None when its file can't be read."""
    return {backend: _scopes(backend, workdir) for backend in ("codex", "claude")}


def check_claude_folder(workdir: Path) -> None:
    """Raise when Claude's settings in the Tag's folder or your own can't be read:
    Claude would skip the file and run without its deny rules."""
    for path in (claude_tag_file(workdir), workdir / ".claude" / "settings.json", claude_personal_file()):
        _read_settings(path)


def claude_all_tags_settings() -> dict[str, Any]:
    """The all-Tags Claude rules as SDK settings; raises if the file can't be read,
    since running without its block rules would give Claude more access."""
    permissions = _read_settings(claude_all_tags_file()).get("permissions", {})
    kept = {key: [v for v in permissions.get(key, []) if isinstance(v, str)]
            for key in ("allow", "ask", "deny", "additionalDirectories") if isinstance(permissions.get(key), list)}
    return {"permissions": {k: v for k, v in kept.items() if v}} if any(kept.values()) else {}


# Claude changes its rules only through you, in Slack or Tag.app; paths are from the Tag's folder.
CLAUDE_KEEPS_OUT = ("Edit(./.claude/settings.local.json)", "Edit(./.claude/settings.json)")


def claude_settings() -> dict[str, Any]:
    """Extra settings for every Claude run: the all-Tags rules, and a block on editing its own."""
    settings = claude_all_tags_settings()
    deny = settings.setdefault("permissions", {}).setdefault("deny", [])
    deny += [rule for rule in CLAUDE_KEEPS_OUT if rule not in deny]
    return settings


def sync_codex_all_tags(workdir: Path) -> None:
    """Give the Tag's folder a current copy of the all-Tags Codex rules, where Codex reads
    them next to the Tag's own. Raises if that can't be done."""
    copy = workdir / ".codex" / "rules" / CODEX_ALL_TAGS_COPY
    try:
        text = codex_all_tags_file().read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    except (OSError, UnicodeDecodeError):
        raise ValueError(f"Couldn't read {codex_all_tags_file()}") from None
    try:
        with _locked(copy):
            if not text.strip():
                copy.unlink(missing_ok=True)
            elif not copy.exists() or copy.read_text(encoding="utf-8") != text:
                _write(copy, text)
    except (OSError, UnicodeDecodeError):
        raise ValueError(f"Couldn't update {copy}") from None


def codex_all_tags_restricts() -> bool:
    """Whether the all-Tags Codex rules block or ask about anything, or can't be read."""
    found = _codex_rules([codex_all_tags_file()])
    return found is None or any(rule["decision"] != "allow" for rule in found)


def codex_tag_restricts(workdir: Path) -> bool:
    """Whether the Codex rules in the Tag's folder (its own, the all-Tags copy, and any
    others there) block or ask about anything, or can't be read, so running without
    them gives Codex more access."""
    files = _codex_folder_files(workdir)
    found = _codex_rules(files) if files is not None else None
    return found is None or any(rule["decision"] != "allow" for rule in found)


def _codex_files(workdir: Path) -> list[Path] | None:
    """Every Codex rules file that applies to a Tag: those in its folder and your own."""
    try:
        personal = sorted(p for p in codex_personal_dir().glob("*.rules") if p.is_file())
    except OSError:
        return None
    folder = _codex_folder_files(workdir)
    return None if folder is None else [*folder, *personal]


def codex_holds(workdir: Path, *, words: list[str] | None = None, host: str | None = None) -> bool:
    """Whether a Codex rule asks about or blocks this command or host, or might: one Tag
    can't read counts. Then only Codex's own answer stands, not a rule saved this run."""
    files = _codex_files(workdir)
    found = _codex_parsed(files) if files is not None else None
    if found is None:
        return True
    for rule, pattern in found:
        if rule["decision"] == "allow":
            continue
        if pattern is None:
            return True
        if isinstance(pattern, str):
            if host is not None and fnmatch.fnmatch(host.lower(), pattern.lower()):
                return True
        elif words is not None and len(pattern) <= len(words) and all(w in p for w, p in zip(words, pattern)):
            return True
    return False


def _codex_words(value: str) -> list[str]:
    try:
        words = shlex.split(value)
    except ValueError:
        words = []
    if (not words or len(value) > 500 or words[0].startswith("-")
            or any(not w or any(ord(c) < 32 for c in w) for w in words)):
        raise ValueError("Write the start of a command, like: git push")
    return words


def _codex_prefix_line(words: list[str], decision: str) -> str:
    """The line Codex itself writes for a saved prefix."""
    native = {"allow": "allow", "block": "forbidden"}[decision]
    return f"prefix_rule(pattern=[{', '.join(json.dumps(w) for w in words)}], decision=\"{native}\")"


def _codex_line_protocol(line: str) -> str | None:
    """The `protocol` argument of the line's `network_rule(...)`, read from the call itself:
    the same words in a justification don't count."""
    try:
        nodes = ast.walk(ast.parse(line.strip()))
    except Exception:  # noqa: BLE001 - a line Tag can't parse has no protocol it can trust
        return None
    for node in nodes:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "network_rule":
            for keyword in node.keywords:
                if keyword.arg == "protocol":
                    try:
                        value = ast.literal_eval(keyword.value)
                    except Exception:  # noqa: BLE001
                        return None
                    return value if isinstance(value, str) else None
    return None


def _codex_lines_without(path: Path, kind: str, value: str,
                         protocol: str | None = None) -> tuple[list[str], bool]:
    """The file's lines minus the rule for `value`. With a `protocol`, a host rule for another protocol stays."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return [], False
    except (OSError, UnicodeDecodeError):
        raise ValueError(f"Couldn't read {path}") from None
    kept = []
    for line in lines:
        found = [rule for rule, _ in _codex_text_rules(line.strip())]
        other = _codex_line_protocol(line) if protocol is not None else None
        if not any(r["kind"] == kind and r["value"] == value for r in found) or (
                other is not None and other != protocol):
            kept.append(line)
        elif len(found) > 1 or ";" in line:
            # Never drop the other rules on that line along with this one.
            raise ValueError(f"{path} has {value!r} on a line with other rules; change that line by hand")
    return kept, len(kept) != len(lines)


def _save_codex(path: Path, kind: str, value: str, line: str, protocol: str | None = None) -> None:
    with _locked(path):
        lines, _ = _codex_lines_without(path, kind, value, protocol)
        _write(path, "\n".join([*lines, line]) + "\n")


def save_codex_command(workdir: Path, words: list[str], decision: str = "allow") -> None:
    """Allow or block commands that start with `words`, for this Tag only."""
    _save_codex(codex_tag_file(workdir), "command", shlex.join(words), _codex_prefix_line(words, decision))


def save_codex_host(workdir: Path, host: str, protocol: str, decision: str) -> None:
    """Always allow or block a network host for this Tag, as Codex's own amendment would globally."""
    if not host or any(c.isspace() or ord(c) < 32 for c in host) or protocol not in CODEX_PROTOCOLS:
        raise ValueError("Codex sent a host rule Tag can't save")
    native = {"allow": "allow", "block": "deny"}[decision]
    _save_codex(codex_tag_file(workdir), "host", host, f"network_rule(host={json.dumps(host)}, "
                f"protocol={json.dumps(CODEX_PROTOCOLS[protocol])}, decision={json.dumps(native)})",
                CODEX_PROTOCOLS[protocol])


def _rule_file(backend: str, workdir: Path | None) -> Path:
    """The file Tag edits: the Tag's own, or the all-Tags one when there's no Tag."""
    if backend == "codex":
        return codex_tag_file(workdir) if workdir is not None else codex_all_tags_file()
    if backend == "claude":
        return claude_tag_file(workdir) if workdir is not None else claude_all_tags_file()
    raise ValueError("Choose codex or claude")


def add_rule(backend: str, workdir: Path | None, decision: str, value: str) -> None:
    """Allow or block something for one Tag, or for all Tags when `workdir` is None.
    A rule already there changes to `decision`."""
    path = _rule_file(backend, workdir)
    if decision not in {"allow", "block"}:
        raise ValueError("Choose allow or block")
    value = value.strip()
    if backend == "codex":
        words = _codex_words(value)
        _save_codex(path, "command", shlex.join(words), _codex_prefix_line(words, decision))
        return
    if len(value) > 500 or not CLAUDE_RULE.fullmatch(value) or any(ord(c) < 32 for c in value):
        raise ValueError("Write a Claude rule, like: Bash(git push:*) or WebFetch(domain:example.com). "
                         "Tool names start with a capital letter.")
    with _locked(path):
        data = _read_settings(path)
        permissions = data.setdefault("permissions", {})
        for key, _ in CLAUDE_DECISIONS:
            if isinstance(permissions.get(key), list):
                permissions[key] = [item for item in permissions[key] if item != value]
        key = "allow" if decision == "allow" else "deny"
        permissions[key] = [*(permissions.get(key) or []), value]
        _write(path, json.dumps(data, indent=2) + "\n")


def remove_rule(backend: str, workdir: Path | None, value: str) -> None:
    """Remove one rule from a Tag's own rules, or the all-Tags ones when `workdir` is None.
    Your own Codex and Claude Code rules are never changed."""
    path = _rule_file(backend, workdir)
    where = "This Tag" if workdir is not None else "All Tags"
    with _locked(path):
        if backend == "codex":
            found = False
            for kind in ("command", "host"):
                lines, removed = _codex_lines_without(path, kind, value)
                if removed:
                    _write(path, "".join(line + "\n" for line in lines))
                    found = True
            if not found:
                raise ValueError(f"{where} has no Codex rule {value!r}")
            return
        data = _read_settings(path)
        permissions = data.get("permissions", {})
        found = False
        for key in ("allow", "ask", "deny", "additionalDirectories"):
            items = permissions.get(key)
            if isinstance(items, list) and value in items:
                permissions[key] = [item for item in items if item != value]
                found = True
        if not found:
            raise ValueError(f"{where} has no Claude rule {value!r}")
        _write(path, json.dumps(data, indent=2) + "\n")


def report(values: dict[str, str], workdir: Path | None = None, *, own_folder: bool = True) -> dict[str, Any]:
    """What the apps show: each backend's settings now, every choice for each, and,
    given the Tag's folder, each agent's rules for the Tag and the ones it inherits.

    A saved value Tag doesn't know is shown as it is, with the reason in `errors`, so
    the apps can offer a valid choice; runs still refuse it. `own_folder` is False when
    the Tag works in a folder of yours, where Tag doesn't keep Codex rules."""
    def options(name: str) -> list[dict[str, str]]:
        return [{"value": value, "label": label, "detail": detail} for value, label, detail in SETTINGS[name][1]]

    errors: dict[str, str] = {}

    def value(name: str) -> str:
        try:
            return _value(name, values)
        except ValueError as exc:
            errors[name] = str(exc)
            return (values.get(SETTINGS[name][0]) or "").strip()

    codex: dict[str, Any] = {"sandbox": value("codex-sandbox"), "approvals": value("codex-approvals"),
                             "reviewer": value("codex-reviewer")}
    claude: dict[str, Any] = {"mode": value("claude-mode"), "chrome": value("claude-chrome")}
    extra: dict[str, Any] = {"errors": errors} if errors else {}
    if workdir is not None:
        found = rules(workdir)
        codex["rules"], claude["rules"] = found["codex"], found["claude"]
        extra["own_folder"] = own_folder
    return {**extra,
        "codex": codex,
        "claude": claude,
        "options": {"codex": {"sandbox": options("codex-sandbox"), "approvals": options("codex-approvals"),
                              "reviewer": options("codex-reviewer")},
                    "claude": {"mode": options("claude-mode"), "chrome": options("claude-chrome")}},
    }


def changes(assignments: list[str]) -> dict[str, str]:
    """``codex-sandbox=read-only claude-mode=auto`` → config keys and values, validated."""
    result: dict[str, str] = {}
    for item in assignments:
        name, _, value = item.partition("=")
        if name not in SETTINGS or (not value and "" not in {o[0] for o in SETTINGS[name][1]}):
            raise ValueError("Use " + ", ".join(f"{n}=VALUE" for n in SETTINGS))
        key = SETTINGS[name][0]
        if error := validation_error(key, value):
            raise ValueError(f"{name}: {error}")
        result[key] = value
    if not result:
        raise ValueError("Use " + ", ".join(f"{n}=VALUE" for n in SETTINGS))
    return result
