"""Translate native Codex decisions into bounded, request-scoped UI choices.

Decision payloads stay in the transport. Slack receives display text and an
opaque choice ID, never an editable permission or policy payload.
"""
from __future__ import annotations

import json
import re
import shlex
from typing import Any
from urllib.parse import urlsplit, urlunsplit

try:
    from .tag_activity_details import preview
except ImportError:
    from tag_activity_details import preview


def approval_choices(method: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    choices: list[dict[str, Any]] = []

    def add(label: str, result: dict[str, Any], detail: str = "", persistent: bool = False) -> None:
        if len(detail) > 1800 or len(choices) >= 20:
            return  # Never hide part of a rule the user would be approving.
        if any(c["result"] == result for c in choices):
            return
        choices.append({"id": str(len(choices)), "label": label, "detail": detail,
                        "persistent": persistent, "result": result})

    if method == "item/permissions/requestApproval":
        permissions = params.get("permissions")
        if isinstance(permissions, dict):
            detail = "Requested permissions: " + json.dumps(permissions, ensure_ascii=False)
            add("Allow for this turn", {"permissions": permissions, "scope": "turn"}, detail)
            add("Allow for this task", {"permissions": permissions, "scope": "session"}, detail)
        add("Deny", {"permissions": {}, "scope": "turn"})
        return choices

    legacy = method in {"execCommandApproval", "applyPatchApproval"}
    command = method in {"item/commandExecution/requestApproval", "execCommandApproval"}
    standard = {
        "accept": "Allow once", "acceptForSession": "Allow for this task",
        "decline": "Deny", "cancel": "Deny and stop",
    }
    # availableDecisions is authoritative, including an empty list. Older
    # servers omit it and use the protocol's standard decisions and proposals.
    decisions = params.get("availableDecisions")
    if decisions is None:
        decisions = list(standard)
        if command:
            prefix = params.get("proposedExecpolicyAmendment", params.get("proposed_execpolicy_amendment"))
            if prefix is not None:
                decisions.insert(2, {"acceptWithExecpolicyAmendment": {"execpolicy_amendment": prefix}})
            proposals = params.get("proposedNetworkPolicyAmendments", [])
            if isinstance(proposals, list):
                decisions.extend({"applyNetworkPolicyAmendment": {"network_policy_amendment": p}}
                                 for p in proposals)
    if not isinstance(decisions, list):
        return []
    for decision in decisions:
        if isinstance(decision, str) and decision in standard:
            native: Any = decision
            if legacy:
                native = {"accept": "approved", "acceptForSession": "approved_for_session",
                          "decline": {"denied": {"rejection": "Denied in Slack"}},
                          "cancel": "abort"}[decision]
            detail = "Applies only to this Tag task, not future Slack requests." if decision == "acceptForSession" else ""
            if decision == "acceptForSession" and not command:
                root = params.get("grantRoot", params.get("grant_root"))
                if isinstance(root, str) and root:
                    detail += " Requested write root: " + root
            add(standard[decision], {"decision": native}, detail)
        elif command and isinstance(decision, dict) and len(decision) == 1:
            amendment = decision.get("acceptWithExecpolicyAmendment")
            network = decision.get("applyNetworkPolicyAmendment")
            if isinstance(amendment, dict) and set(amendment) == {"execpolicy_amendment"}:
                prefix = amendment["execpolicy_amendment"]
                if (isinstance(prefix, list) and prefix
                        and all(isinstance(p, str) and p and not any(ord(c) < 32 for c in p) for p in prefix)):
                    native = ({"approved_execpolicy_amendment": {"proposed_execpolicy_amendment": prefix}}
                              if legacy else decision)
                    add("Always allow this prefix", {"decision": native},
                        "Save a rule for future matching commands: " + shlex.join(prefix), True)
            elif isinstance(network, dict) and set(network) == {"network_policy_amendment"}:
                rule = network["network_policy_amendment"]
                if (isinstance(rule, dict) and set(rule) == {"host", "action"}
                        and isinstance(rule["host"], str) and rule["host"]
                        and not any(c.isspace() or ord(c) < 32 for c in rule["host"])
                        and rule["action"] in ("allow", "deny")):
                    native = {"network_policy_amendment": network} if legacy else decision
                    verb = "allow" if rule["action"] == "allow" else "deny"
                    add(f"Always {verb} this host", {"decision": native},
                        f"Save a persistent network {verb} rule for host: " + rule["host"], True)
    return choices


def public_approval_choices(choices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in choice.items() if k != "result"} for choice in choices]


def _review_text(value: Any, fallback: str, limit: int = 900) -> str:
    """Bound and redact review text before private Slack delivery."""
    if not isinstance(value, str) or not value.strip():
        return fallback
    if len(value) > 8000:
        return "Details omitted because Codex returned an oversized value."
    value = re.sub(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
                   "[private key redacted]", value)
    value = re.sub(r"(?i)([\"'])(?:authorization|cookie|set-cookie)\s*:[^\r\n]*?\1",
                   "[header redacted]", value)
    value = re.sub(r"(?i)(\bbasic\s+)[A-Za-z0-9+/=]+", r"\1[redacted]", value)
    value = preview(value, limit=8000)
    def scrub_url(match: re.Match[str]) -> str:
        try:
            url = urlsplit(match.group(0))
            host = url.hostname or ""
            port = f":{url.port}" if url.port else ""
            return urlunsplit((url.scheme, host + port, url.path, "", ""))
        except ValueError:
            return "[URL omitted]"
    value = re.sub(r"https?://[^\s<>\"']+", scrub_url, value)
    value = re.sub(
        r"(?i)((?:--)?(?:password|passwd|token|secret|api[_-]?key|cookie|authorization)"
        r"[\"']?\s*(?:[:=]\s*|\s+))(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        r"\1[redacted]", value,
    )
    value = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", value)
    return preview(value, limit=limit)


def sanitize_review_details(value: Any) -> dict[str, str]:
    value = value if isinstance(value, dict) else {}
    return {
        "action": _review_text(value.get("action"), "Codex did not provide action details."),
        "reason": _review_text(value.get("reason"), "Codex did not provide a reason."),
    }


def auto_review_details(event: dict[str, Any]) -> dict[str, str]:
    """Summarize only the denied action and its explicit review rationale."""
    action = event.get("action")
    action = action if isinstance(action, dict) else {}
    kind = action.get("type")
    summary = "Codex did not provide action details."
    if kind == "command" and isinstance(action.get("command"), str):
        summary = "Run command: " + action["command"]
    elif kind == "execve" and isinstance(action.get("program"), str):
        summary = "Run program: " + action["program"]
        argv = action.get("argv")
        if isinstance(argv, list) and all(isinstance(arg, str) for arg in argv):
            summary += "\nArguments: " + shlex.join(argv)
    elif kind == "write_stdin":
        summary = "Send input to terminal process " + str(action.get("process_id", "unknown"))
        summary += " (input content withheld)."
    elif kind == "apply_patch":
        files = action.get("files")
        if isinstance(files, list) and all(isinstance(path, str) for path in files):
            summary = "Change files: " + ", ".join(files)
    elif kind == "network_access":
        summary = "Connect to host: " + str(action.get("host", "unknown"))
        if isinstance(action.get("port"), int):
            summary += ":" + str(action["port"])
        if isinstance(action.get("protocol"), str):
            summary += " via " + action["protocol"]
    elif kind == "mcp_tool_call":
        server, tool = action.get("server"), action.get("tool_name")
        if isinstance(server, str) and isinstance(tool, str):
            summary = "Use connected tool: " + server + "/" + tool
            title = action.get("tool_title")
            if isinstance(title, str) and title.strip():
                summary = title + "\nTool: " + server + "/" + tool
    return sanitize_review_details({"action": summary, "reason": event.get("rationale")})
