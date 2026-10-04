"""Allowlisted, bounded Codex approval context for requester-only Slack views."""
from __future__ import annotations

import re
import shlex
from typing import Any

try:
    from .tag_error_reporting import redact_sensitive_text
except ImportError:
    from tag_error_reporting import redact_sensitive_text


def _safe(value: Any, limit: int = 180) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    value = redact_sensitive_text(value, limit=limit + 1)
    value = re.sub(r"[<>*`&]", "", value).strip()
    return value[:limit] + ("…" if len(value) > limit else "")


def _command(value: Any) -> str:
    if isinstance(value, list):
        value = value[0] if len(value) == 1 else None
    if not isinstance(value, str) or len(value) > 8000:
        return ""
    try:
        words = shlex.split(value)
    except ValueError:
        return ""
    if not words:
        return ""
    executable = _safe(words[0].rsplit("/", 1)[-1], 60)
    if executable not in {
        "bash", "cat", "cp", "curl", "find", "git", "go", "head", "ls", "mkdir",
        "mv", "npm", "pnpm", "python", "python3", "pytest", "rm", "sed",
        "sh", "swift", "touch", "uv", "wget", "yarn", "zsh",
    }:
        return "Command name and arguments withheld"
    # Arguments can contain credentials or file contents. The executable is
    # sufficient to identify the command class without forwarding those values.
    return f"{executable} (arguments withheld)" if len(words) > 1 else executable


def approval_details(method: str, params: Any) -> dict[str, str]:
    """Extract only documented fields; never copy a raw request into Slack."""
    if not isinstance(params, dict):
        params = {}
    details: dict[str, str] = {}
    if method in {"item/commandExecution/requestApproval", "execCommandApproval"}:
        details["action"] = "Run a command"
        command = _command(params.get("command"))
        if command:
            details["command"] = command
        actions = params.get("commandActions")
        if isinstance(actions, list):
            for action in actions[:3]:
                if isinstance(action, dict) and action.get("type") in {"read", "listFiles", "search"}:
                    target = _safe(action.get("path"))
                    if target:
                        details["target"] = target
                        break
        cwd = _safe(params.get("cwd"))
        if cwd:
            details["working_directory"] = cwd
        context = params.get("networkApprovalContext")
        if isinstance(context, dict):
            host = _safe(context.get("host"), 120)
            if host:
                details["network_host"] = host
    elif method in {"item/fileChange/requestApproval", "applyPatchApproval"}:
        details["action"] = "Change files"
        root = _safe(params.get("grantRoot"))
        if root:
            details["requested_write_root"] = root
        changes = params.get("fileChanges")
        if isinstance(changes, dict):
            paths = [_safe(path) for path in list(changes)[:3]]
            paths = [path for path in paths if path]
            if paths:
                details["files"] = ", ".join(paths) + ("…" if len(changes) > 3 else "")
    elif method == "item/permissions/requestApproval":
        details["action"] = "Use additional permissions"
        permissions = params.get("permissions")
        if isinstance(permissions, dict):
            network = permissions.get("network")
            if isinstance(network, dict) and network.get("enabled") is True:
                details["network"] = "Network access requested"
            filesystem = permissions.get("fileSystem")
            if isinstance(filesystem, dict):
                for access in ("read", "write"):
                    paths = filesystem.get(access)
                    if isinstance(paths, list):
                        safe_paths = [_safe(path) for path in paths[:3]]
                        if safe_paths:
                            details[f"filesystem_{access}"] = ", ".join(safe_paths)
                entries = filesystem.get("entries")
                if isinstance(entries, list):
                    formatted = []
                    for entry in entries[:3]:
                        if isinstance(entry, dict):
                            path_value = entry.get("path")
                            if isinstance(path_value, dict):
                                path_value = (path_value.get("path") if path_value.get("type") == "path"
                                              else path_value.get("pattern") if path_value.get("type") == "glob_pattern"
                                              else None)
                            path = _safe(path_value)
                            access = _safe(entry.get("access"), 30)
                            if path:
                                formatted.append(f"{access}: {path}" if access else path)
                    if formatted:
                        details["filesystem_entries"] = ", ".join(formatted)
        cwd = _safe(params.get("cwd"))
        if cwd:
            details["working_directory"] = cwd
    else:
        return {"action": "Unsupported approval request"}
    reason = _safe(params.get("reason"), 240)
    if reason:
        details["reason"] = reason
    return details
