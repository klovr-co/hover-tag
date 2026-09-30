"""Bounded tool-item detail previews for the requester's private activity view."""
from __future__ import annotations

import json
import math
import re
import shlex
from typing import Any

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


def command_identity(command: Any, depth: int = 0) -> str:
    """Name simple commands briefly without copying their full arguments."""
    if not isinstance(command, str) or len(command) > 8_000 or depth > 1:
        return "Command"
    try:
        argv = shlex.split(command)
    except ValueError:
        return "Command"
    if not argv or any(char in command for char in "\n\r;&|<>`$"):
        return "Command"
    executable = argv[0].replace("\\", "/").rsplit("/", 1)[-1]
    if executable in {"sh", "bash", "zsh"} and len(argv) == 3 and argv[1] in {"-c", "-lc"}:
        return command_identity(argv[2], depth + 1)
    if executable in {"cat", "head", "tail"} and len(argv) >= 2:
        target = argv[-1].replace("\\", "/").rsplit("/", 1)[-1]
        if target and not target.startswith("-"):
            return preview(f"{executable} {target}", limit=MAX_TOOL_CHARS)
    return preview(executable, limit=MAX_TOOL_CHARS) if executable else "Command"
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
        if completed and "changes" in item:
            details["output"] = preview(item["changes"])
    elif item_type == "imageView":
        details["tool"] = "Image view"
        if not completed and "path" in item:
            details["input"] = preview({"path": item["path"]})
    return details
