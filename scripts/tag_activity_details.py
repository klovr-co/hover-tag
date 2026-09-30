"""Bounded tool-item detail previews for the requester's private activity view."""
from __future__ import annotations

import ast
import json
import math
import re
import shlex
from typing import Any
from urllib.parse import urlsplit

try:
    from .tag_error_reporting import redact_sensitive_text
except ImportError:  # Direct script execution does not create a package context.
    from tag_error_reporting import redact_sensitive_text


MAX_DETAIL_CHARS = 900
MAX_TOOL_CHARS = 160
MAX_RAW_STRING_CHARS = 8_000
SECRET_KEY_RE = re.compile(
    r"(?:token|secret|password|passwd|api[_-]?key|authorization|cookie|credential|private[_-]?key)",
    re.IGNORECASE,
)


def _command_segments(command: str) -> list[tuple[str, str]]:
    """Split only top-level shell operators; never inspect inline program bodies."""
    segments: list[tuple[str, str]] = []
    quote = None
    escaped = False
    start = 0
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char in "`(){}":
            return []  # Dynamic expressions and shell control structures are opaque.
        elif char in "<>":
            # A heredoc is commonly an inline Python program. Stop before its body.
            if command[index:index + 2] == "<<":
                segments.append((command[start:index], ""))
                return segments
            return []  # Redirection may change a read into a write.
        elif char == "#" and (index == 0 or command[index - 1].isspace()):
            end = command.find("\n", index)
            if end == -1:
                command = command[:index]
                break
            segments.append((command[start:index], ";"))
            start = end + 1
            index = end
        elif char in ";&|\n":
            separator = char
            if char in "&|" and command[index:index + 2] == char * 2:
                separator = char * 2
                index += 1
            elif char == "&":
                return []  # Background jobs have a separate lifecycle.
            segments.append((command[start:index + 1 - len(separator)], separator))
            start = index + 1
        index += 1
    if quote or escaped:
        return []
    segments.append((command[start:], ""))
    return [(part, separator) for part, separator in segments if part.strip()]


def _unwrap_command(argv: list[str]) -> list[str]:
    """Remove known execution wrappers without exposing their options or values."""
    for _ in range(8):
        while argv and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*=.*", argv[0]):
            argv = argv[1:]
        if not argv:
            return []
        program = argv[0].replace("\\", "/").rsplit("/", 1)[-1]
        if program in {"command", "exec", "nohup"}:
            argv = argv[1:]
            if argv[:1] == ["--"]:
                argv = argv[1:]
            if argv and argv[0].startswith("-"):
                return []
        elif ((program in {"uv", "poetry"} and argv[1:2] == ["run"])
              or (program in {"npm", "pnpm", "yarn", "bun"} and argv[1:2] == ["exec"])):
            argv = argv[2:]
            if argv[:1] == ["--"]:
                argv = argv[1:]
            if argv and argv[0].startswith("-"):
                return []
        elif program == "npx":
            argv = argv[1:]
            while argv and argv[0] in {"-y", "--yes", "--no-install", "--"}:
                argv = argv[1:]
            if argv and argv[0].startswith("-"):
                return []
        elif program in {"env", "sudo"}:
            options = {"-u", "--unset", "-C", "--chdir"} if program == "env" else {
                "-u", "--user", "-g", "--group", "-h", "--host", "-D", "--chdir"}
            flags = {"-i", "--ignore-environment"} if program == "env" else {"-n", "-E", "-H", "-S"}
            argv = argv[1:]
            while argv and argv[0].startswith("-"):
                option = argv[0]
                if option == "--":
                    argv = argv[1:]
                    break
                if option in options and len(argv) > 1:
                    argv = argv[2:]
                elif option in flags or any(option.startswith(flag + "=") for flag in options if flag.startswith("--")):
                    argv = argv[1:]
                else:
                    return []
        else:
            return argv
    return []


def _display_filename(value: str) -> str:
    """Only literal basenames, never shell expansions or URL credentials."""
    if not value or any(char in value for char in "$`\n\r&|;<>") or "://" in value:
        return ""
    parts = value.replace("\\", "/").rstrip("/").split("/")
    name = parts[-1]
    if name == "SKILL.md" and len(parts) > 1:
        parent = parts[-2]
        if parent not in {".", ".."} and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", parent):
            return f"{parent}/SKILL.md"
    return name if name not in {".", ".."} else ""


def _python_inline_identity(executable: str, source: str) -> str:
    """Recognize literal file operands in Python syntax without executing it."""
    names: list[str] = []
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return f"{executable} (inline)"
    # Resolve only unambiguous literal assignments, never evaluate expressions.
    bindings: dict[str, list[ast.expr]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings.setdefault(target.id, []).append(node.value)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        function = node.func
        known = (isinstance(function, ast.Name) and function.id in {"open", "Path"}) or (
            isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name)
            and (function.value.id, function.attr) in {
                ("pathlib", "Path"), ("io", "open"), ("runpy", "run_path"),
                ("Image", "open"), ("pd", "read_csv"), ("pd", "read_json"),
                ("pd", "read_excel"), ("pandas", "read_csv"), ("pandas", "read_json"),
                ("pandas", "read_excel"), ("np", "load"), ("numpy", "load"),
            }
        )
        operand = node.args[0]
        if isinstance(operand, ast.Name) and len(bindings.get(operand.id, [])) == 1:
            operand = bindings[operand.id][0]
        if known and isinstance(operand, ast.Constant) and isinstance(operand.value, str):
            name = _display_filename(operand.value)
            if name and name not in names:
                names.append(name)
    if not names:
        return f"{executable} (inline)"
    suffix = f" (+{len(names) - 3} more)" if len(names) > 3 else ""
    return f"{executable} (inline) · " + ", ".join(names[:3]) + suffix


def _http_identity(executable: str, args: list[str]) -> str:
    """Retain request method, public destination identity and output basename.

    Header values, payloads, URL credentials, query strings, fragments, and
    directory paths never become shared row text. Unknown options fail closed.
    """
    targets: list[str] = []
    output = ""
    method = "GET"
    explicit_method = False
    value_options = {"-H", "--header", "-u", "--user", "--proxy-user", "-A", "--user-agent",
                     "-e", "--referer", "-b", "--cookie", "-c", "--cookie-jar", "-x", "--proxy",
                     "--connect-timeout", "--max-time", "-m", "--retry", "--retry-delay",
                     "--cacert", "--cert", "--key", "--resolve", "--connect-to",
                     "--max-redirs", "--limit-rate", "-w", "--write-out", "--range", "-r"}
    data_options = {"-d", "--data", "--data-raw", "--data-binary", "--data-urlencode",
                    "--data-ascii", "-F", "--form", "--form-string"}
    flags = {"--silent", "--show-error", "--fail", "--fail-with-body", "--location",
             "--compressed", "--insecure", "--http1.1", "--http2", "--globoff",
             "--no-progress-meter", "--remote-name", "--create-dirs"}
    index = 0
    operands_only = False
    while index < len(args):
        arg = args[index]
        option, equal, attached = arg.partition("=") if arg.startswith("--") else (arg, "", "")
        short_value_options = {"-o", "-X", "-T"} | value_options | data_options
        if not arg.startswith("--") and len(arg) > 2 and arg[:2] in short_value_options:
            option, equal, attached = arg[:2], "=", arg[2:]
        needs_value = option in value_options | data_options | {"-o", "--output", "-X", "--request", "-T", "--upload-file", "--url"}
        if not operands_only and needs_value:
            if equal:
                value = attached
            elif index + 1 < len(args):
                index += 1
                value = args[index]
            else:
                return executable
            if option in {"-o", "--output"}:
                output = _display_filename(value) if value != "-" else ""
            elif option in {"-X", "--request"}:
                if value.upper() not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
                    return executable
                method, explicit_method = value.upper(), True
            elif option in data_options and not explicit_method:
                method = "POST"
            elif option in {"-T", "--upload-file"} and not explicit_method:
                method = "PUT"
            elif option == "--url":
                targets.append(value)
        elif not operands_only and arg == "--":
            operands_only = True
        elif not operands_only and (arg in {"-I", "--head"} or re.fullmatch(r"-[fsSLkvOg]*I[fsSLkvOg]*", arg)):
            method, explicit_method = "HEAD", True
        elif not operands_only and arg in {"-G", "--get"}:
            method, explicit_method = "GET", True
        elif not operands_only and (arg in flags or re.fullmatch(r"-[fsSLkvOg]+", arg)):
            pass
        elif not operands_only and arg.startswith("-"):
            return executable
        else:
            targets.append(arg)
        index += 1
    destinations: list[str] = []
    for target in targets:
        if any(char in target for char in "$`\n\r&|;<>"):
            # Ampersands in the query are fine: queries are discarded below.
            target = target.split("?", 1)[0].split("#", 1)[0]
            if any(char in target for char in "$`\n\r&|;<>"):
                continue
        try:
            url = urlsplit(target if "://" in target else "https://" + target)
            host = url.hostname
        except ValueError:
            continue
        if url.scheme not in {"http", "https"} or not host or not re.fullmatch(r"[A-Za-z0-9.:-]+", host):
            continue
        filename = _display_filename(url.path)
        destination = host + ("/" + filename if filename else "")
        if destination not in destinations:
            destinations.append(destination)
    if not destinations:
        return executable
    suffix = f" (+{len(destinations) - 2} more)" if len(destinations) > 2 else ""
    return f"HTTP {method} · " + ", ".join(destinations[:2]) + suffix + (f" → {output}" if output else "")


def _target_operands(args: list[str], *, values: set[str] = frozenset(),
                     flags: set[str] = frozenset(), skip_first: bool = False) -> list[str]:
    """Extract operands only when every option's arity is known."""
    operands: list[str] = []
    index = 0
    options = True
    while index < len(args):
        arg = args[index]
        if options and arg == "--":
            options = False
        elif options and arg in values:
            if index + 1 >= len(args):
                return []
            index += 1
        elif options and arg.startswith("--") and arg.partition("=")[0] in values and "=" in arg:
            pass
        elif options and arg.startswith("-"):
            if arg not in flags:
                return []
        else:
            operands.append(arg)
        index += 1
    if skip_first:
        operands = operands[1:]
    return [name for value in operands if (name := _display_filename(value))]


def _with_targets(identity: str, targets: list[str]) -> str:
    targets = list(dict.fromkeys(targets))
    return identity + (" · " + ", ".join(targets[:3]) +
                       (f" (+{len(targets) - 3} more)" if len(targets) > 3 else "") if targets else "")


def _simple_command_identity(argv: list[str]) -> str:
    executable = argv[0].replace("\\", "/").rsplit("/", 1)[-1]
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+", executable):
        return "Command"
    args = argv[1:]
    if args and args[0] in {"--help", "--version"}:
        return f"{executable} {args[0]}"
    if executable in {"if", "for", "while", "until", "case", "eval", "source", "exit", "return", "break", "continue", "trap", "set"}:
        return "Command"
    if executable == "curl":
        return _http_identity(executable, args)
    if executable == "wget":
        translated: list[str] = []
        index = 0
        while index < len(args):
            arg = args[index]
            if arg in {"-q", "--quiet", "--no-verbose", "-nv"}:
                translated.append("--silent")
            elif arg in {"-O", "--output-document"} and index + 1 < len(args):
                index += 1
                translated.extend(["--output", args[index]])
            elif arg.startswith("--output-document="):
                translated.append("--output=" + arg.partition("=")[2])
            elif arg.startswith("-"):
                return executable
            else:
                translated.append(arg)
            index += 1
        return _http_identity(executable, translated)
    if executable in {"ls", "wc", "du", "file", "stat"}:
        return _with_targets(executable, _target_operands(args,
            flags={"-l", "-a", "-la", "-al", "-lh", "-lah", "-h", "-s", "-sh", "-b", "-c", "-w", "-L"}))
    if executable in {"rg", "grep"}:
        pattern_options = {"-e", "--regexp", "-f", "--file"}
        explicit_pattern = any(arg.partition("=")[0] in pattern_options for arg in args)
        list_files = executable == "rg" and "--files" in args
        targets = _target_operands(args, values=pattern_options | {"-g", "--glob", "-t", "--type", "-A", "-B", "-C", "-m", "--max-count"},
            flags={"-n", "-i", "-l", "-r", "-R", "-rn", "-rin", "-v", "-F", "-E", "-q", "--hidden", "--files", "--no-heading", "--line-number"},
            skip_first=not (explicit_pattern or list_files))
        return _with_targets("rg --files" if list_files else executable, targets)
    if executable in {"sed", "awk", "jq"}:
        explicit = any(arg in {"-e", "-f"} for arg in args)
        return _with_targets(executable, _target_operands(args, values={"-e", "-f"},
            flags={"-n", "-r", "-R", "-c", "-s", "-S", "-E"}, skip_first=not explicit))
    if executable == "find":
        roots = []
        for arg in args:
            if arg.startswith("-"):
                break
            if name := _display_filename(arg):
                roots.append(name)
        return _with_targets(executable, roots)
    if executable in {"pytest", "jest", "vitest", "eslint", "tsc"}:
        if executable == "vitest" and args[:1] in (["run"], ["watch"]):
            args = args[1:]
        return _with_targets(executable, _target_operands(args,
            values={"-k", "-m", "--config", "-c", "--maxfail", "--project", "-p", "--testNamePattern", "-t"},
            flags={"-q", "-v", "-vv", "-x", "--noEmit", "--fix", "--runInBand", "--watch", "--collect-only"}))
    if executable in {"bash", "sh", "zsh", "dash", "tsx", "ts-node", "php", "lua"}:
        targets = _target_operands(args, flags={"-x", "-e", "-n", "--transpile-only"})
        return _with_targets(executable, targets[:1])
    if executable == "git":
        while args and args[0].startswith("-"):
            if args[0] in {"-C", "-c", "--git-dir", "--work-tree"} and len(args) > 1:
                args = args[2:]
            elif args[0] in {"--no-pager", "--paginate", "--no-optional-locks"} or args[0].startswith(("--git-dir=", "--work-tree=")):
                args = args[1:]
            else:
                return "git"
        if args and args[0] in {"status", "diff", "log", "show", "add", "commit", "fetch", "pull", "push", "checkout", "switch", "restore", "reset", "merge", "rebase", "branch", "rev-parse", "ls-files"}:
            targets = []
            if "--" in args and args[0] in {"diff", "log", "add", "restore", "ls-files"}:
                targets = _target_operands(args[args.index("--") + 1:])
            elif args[0] in {"add", "restore"}:
                targets = _target_operands(args[1:], flags={"--staged", "--worktree"})
            return _with_targets(f"git {args[0]}", targets)
    if executable in {"npm", "pnpm", "yarn", "bun", "cargo", "go", "make", "ruff"}:
        custom_script = args[:1] in (["run"], ["run-script"])
        if custom_script:
            args = args[1:]
        if args and args[0] in {"test", "t", "build", "check", "lint", "typecheck", "type-check", "format", "fmt", "install", "ci"}:
            targets = _target_operands(args[1:], flags={"--fix", "--quiet", "-q"}) if args[0] not in {"install", "ci"} else []
            return _with_targets(f"{executable} {args[0]}", targets)
        if custom_script and args and re.fullmatch(r"[A-Za-z0-9_.:-]+", args[0]):
            return f"Package script · {args[0]}"
        if executable == "make":
            return _with_targets("make", [target for target in _target_operands(args, flags={"-s", "--silent"}) if "=" not in target][:1])
    if executable in {"cat", "head", "tail", "cp", "mv", "mkdir", "rm", "touch"}:
        operands: list[str] = []
        destination = None
        index = 0
        options = True
        while index < len(args):
            arg = args[index]
            if options and arg == "--":
                options = False
            elif options and arg in {"-n", "-c", "--lines", "--bytes"} and executable in {"head", "tail"}:
                index += 1
            elif options and executable in {"cp", "mv"} and arg in {"-t", "--target-directory", "-S", "--suffix"}:
                index += 1
                if arg in {"-t", "--target-directory"} and index < len(args):
                    destination = args[index]
            elif options and executable in {"cp", "mv"} and arg.startswith("--target-directory="):
                destination = arg.partition("=")[2]
            elif options and executable == "mkdir" and arg in {"-m", "--mode"}:
                index += 1
            elif not (options and arg.startswith("-")):
                name = _display_filename(arg)
                if name and not any(char in name for char in "$`\n\r&|;"):
                    operands.append(name)
            index += 1
        if operands:
            if destination is not None:
                if any(char in destination for char in "$`\n\r&|;"):
                    return executable
                target = destination.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            else:
                target = operands[-1] if executable in {"cp", "mv"} else ", ".join(operands[:3])
            if executable not in {"cp", "mv"} and len(operands) > 3:
                target += f" (+{len(operands) - 3} more)"
            return f"{executable} {target}"
    if executable == "node":
        while args:
            if args[0] in {"--require", "-r", "--import", "--loader"} and len(args) > 1:
                args = args[2:]
            elif args[0] in {"--no-warnings", "--trace-warnings", "--enable-source-maps"} or args[0].startswith(("--require=", "--import=", "--loader=")):
                args = args[1:]
            else:
                break
        if args[:1] == ["--test"]:
            return _with_targets("Tests", _target_operands(args[1:]))
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", executable) or executable in {"node", "ruby", "perl"}:
        while args:
            if args[0] in {"-u", "-B", "-E", "-I", "-O", "-OO", "-s", "-S", "-q", "--"}:
                stop = args[0] == "--"
                args = args[1:]
                if stop:
                    break
            elif args[0] in {"-W", "-X"} and len(args) > 1:
                args = args[2:]
            elif args[0].startswith(("-W", "-X")) or re.fullmatch(r"-[uBEIOsqS]+", args[0]):
                args = args[1:]
            else:
                break
        if args and args[0] == "-m" and len(args) > 1 and re.fullmatch(r"[A-Za-z0-9_.]+", args[1]):
            return f"{executable} -m {args[1]}"
        if args and args[0] in {"-c", "-e", "-"}:
            if executable.startswith("python") and args[0] == "-c" and len(args) > 1:
                return _python_inline_identity(executable, args[1])
            return f"{executable} (inline)"
        if args and not args[0].startswith("-") and not any(char in args[0] for char in "$`\n\r&|;"):
            target = args[0].replace("\\", "/").rsplit("/", 1)[-1]
            return f"{executable} {target}"
    return executable


def command_identity(command: Any, depth: int = 0) -> str:
    """Summarize bounded shell commands; arguments, inline code and paths stay private."""
    if not isinstance(command, str) or len(command) > MAX_RAW_STRING_CHARS or depth > 4:
        return "Command"
    heredoc = re.fullmatch(r"([^\n]+?)\s*<<\s*([\"\']?)([A-Za-z_][A-Za-z_0-9]*)\2\s*\n([\s\S]*?)\n\3\s*", command)
    if heredoc:
        try:
            argv = _unwrap_command(shlex.split(heredoc[1]))
        except ValueError:
            argv = []
        if argv and re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", argv[0].rsplit("/", 1)[-1]):
            identity = _simple_command_identity(argv)
            if identity.endswith("(inline)") or len(argv) == 1:
                return preview(_python_inline_identity(argv[0].rsplit("/", 1)[-1], heredoc[4]), limit=MAX_TOOL_CHARS)
    segments = _command_segments(command)
    if not segments or len(segments) > 16:
        return "Command"
    identities: list[str] = []
    for part, separator in segments:
        try:
            argv = _unwrap_command(shlex.split(part))
        except ValueError:
            return "Command"
        if not argv:
            continue
        executable = argv[0].replace("\\", "/").rsplit("/", 1)[-1]
        if executable in {"cd", "export"} and separator in {"&&", ";", "\n"}:
            continue
        if executable in {"sh", "bash", "zsh", "dash"} and len(argv) == 3 and argv[1] in {"-c", "-lc", "-cl"}:
            identity = command_identity(argv[2], depth + 1)
        else:
            identity = _simple_command_identity(argv)
        if identity == "Command":
            return identity
        identities.append(identity)
        if separator and len(identities) < 6:
            identities.append(" | " if separator == "|" else " || " if separator == "||" else " && ")
        if len(identities) >= 6:
            identities.append("more commands")
            break
    return preview("".join(identities).rstrip(" &|"), limit=MAX_TOOL_CHARS) if identities else "Command"


def _scrub(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return "[nested value omitted]"
    if isinstance(value, str):
        if len(value) > MAX_RAW_STRING_CHARS:
            return "[large value omitted]"
        if value.lstrip().startswith(("{", "[")):
            try:
                nested = json.loads(value)
            except ValueError:
                pass
            else:
                if isinstance(nested, (dict, list)):
                    return _scrub(nested, depth + 1)
        return redact_sensitive_text(
            value, limit=MAX_RAW_STRING_CHARS, preserve_whitespace=True,
        )
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, child) in enumerate(value.items()):
            if index >= 30:
                result["…"] = "[additional fields omitted]"
                break
            name = str(key)[:80]
            result[name] = "[redacted]" if SECRET_KEY_RE.search(name) else _scrub(child, depth + 1)
        return result
    if isinstance(value, list):
        result = [_scrub(child, depth + 1) for child in value[:20]]
        if len(value) > 20:
            result.append("[additional items omitted]")
        return result
    if isinstance(value, float) and not math.isfinite(value):
        return "[non-finite number omitted]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[unsupported value]"


def preview(value: Any, *, limit: int = MAX_DETAIL_CHARS) -> str:
    """Serialize bounded, redacted structure without retaining the raw value."""
    scrubbed = _scrub(value)
    rendered = scrubbed if isinstance(scrubbed, str) else json.dumps(
        scrubbed, ensure_ascii=False, indent=2, allow_nan=False,
    )
    return rendered if len(rendered) <= limit else rendered[: limit - 14] + "… [truncated]"


def sanitize_activity_details(details: Any) -> dict[str, str]:
    """Recheck untrusted normalized events before saving them to the store."""
    if not isinstance(details, dict):
        return {}
    result: dict[str, str] = {}
    for field in ("tool", "input", "output"):
        value = details.get(field)
        if isinstance(value, str) and value:
            result[field] = preview(value, limit=MAX_TOOL_CHARS if field == "tool" else MAX_DETAIL_CHARS)
    return result


def item_activity_details(item: dict[str, Any], *, completed: bool) -> dict[str, str]:
    """Extract documented App Server tool fields; never include reasoning items."""
    item_type = item.get("type")
    details: dict[str, str] = {}
    if item_type == "mcpToolCall":
        server, tool = item.get("server"), item.get("tool")
        if isinstance(server, str) and isinstance(tool, str):
            details["tool"] = preview(f"{server}/{tool}", limit=MAX_TOOL_CHARS)
        elif isinstance(tool, str):
            details["tool"] = preview(tool, limit=MAX_TOOL_CHARS)
        if not completed and "arguments" in item:
            details["input"] = preview(item["arguments"])
        if completed:
            result = {key: item[key] for key in ("result", "error") if key in item}
            if result:
                details["output"] = preview(result)
    elif item_type == "dynamicToolCall":
        if isinstance(item.get("tool"), str):
            details["tool"] = preview(item["tool"], limit=MAX_TOOL_CHARS)
        if not completed and "arguments" in item:
            details["input"] = preview(item["arguments"])
        if completed:
            result = {key: item[key] for key in ("contentItems", "success") if key in item}
            if result:
                details["output"] = preview(result)
    elif item_type == "commandExecution":
        if not completed or isinstance(item.get("command"), str):
            details["tool"] = command_identity(item.get("command"))
        if not completed:
            command = {key: item[key] for key in ("command", "cwd") if key in item}
            if command:
                details["input"] = preview(command)
        else:
            output = item.get("aggregatedOutput")
            exit_code = item.get("exitCode")
            if isinstance(output, str):
                prefix = f"Exit code: {exit_code}\n" if isinstance(exit_code, int) else ""
                details["output"] = preview(prefix + output)
            elif isinstance(exit_code, int):
                details["output"] = f"Exit code: {exit_code}"
    elif item_type == "webSearch":
        details["tool"] = "Web search"
        if not completed:
            query = {key: item[key] for key in ("query", "action") if key in item}
            if query:
                details["input"] = preview(query)
    elif item_type == "fileChange":
        details["tool"] = "File change"
        changes = item.get("changes")
        if isinstance(changes, list):
            names = [change["path"].replace("\\", "/").rsplit("/", 1)[-1]
                     for change in changes if isinstance(change, dict) and isinstance(change.get("path"), str)]
            if names:
                suffix = f" (+{len(names) - 3} more)" if len(names) > 3 else ""
                reported_kinds = [change.get("kind", {}).get("type") if isinstance(change.get("kind"), dict)
                                  else change.get("kind") for change in changes if isinstance(change, dict)]
                kinds = {kind if isinstance(kind, str) else None for kind in reported_kinds}
                action = "create" if kinds == {"add"} else "delete" if kinds == {"delete"} else "change"
                if all(isinstance(change, dict) and isinstance(change.get("kind"), dict)
                       and isinstance(change["kind"].get("movePath"), str) and change["kind"]["movePath"]
                       and isinstance(change.get("path"), str) for change in changes):
                    action = "move"
                    names = [change["path"].replace("\\", "/").rsplit("/", 1)[-1] + " → "
                             + change["kind"]["movePath"].replace("\\", "/").rsplit("/", 1)[-1]
                             for change in changes]
                details["tool"] = preview(f"File {action} · " + ", ".join(names[:3]) + suffix, limit=MAX_TOOL_CHARS)
        if completed and "changes" in item:
            details["output"] = preview(item["changes"])
    elif item_type == "imageView":
        details["tool"] = "Image view"
        if isinstance(item.get("path"), str) and (name := _display_filename(item["path"])):
            details["tool"] = preview("Image view · " + name, limit=MAX_TOOL_CHARS)
        if not completed and "path" in item:
            details["input"] = preview({"path": item["path"]})
    return details
