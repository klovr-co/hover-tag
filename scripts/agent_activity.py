"""Backend-neutral activity labels and approval timing shared by agent backends.

Labels are a fixed public vocabulary derived from identifiable tool actions,
never from tool arguments or results. Backends translate their native tool
events into App Server item shapes before labeling them.
"""

from __future__ import annotations

import re
import shlex
from typing import Any


INTERRUPT_GRACE_SECONDS = 5.0
APPROVAL_POLL_SECONDS = 0.1
APPROVAL_TIMEOUT_SECONDS = 600.0

# Public display vocabulary, never populated from tool arguments or results.
MCP_SERVICE_NAMES = {
    "github": "GitHub", "slack": "Slack", "linear": "Linear",
    "notion": "Notion", "datadog": "Datadog", "mfs": "connected knowledge",
    "playwright": "Playwright", "context7": "Context7",
}
MCP_TOOL_NAMES = frozenset({
    "search", "fetch", "read_resource", "list_resources", "search_issues",
    "get_issue", "list_issues", "create_issue", "update_issue",
    "search_code", "get_file_contents", "list_pull_requests", "pull_request_read",
    "create_pull_request", "query_metrics", "search_logs", "list_dashboards",
    "get_document", "search_pages", "fetch_documentation", "resolve_library_id",
    "resolve-library-id", "query-docs", "browser_navigate", "browser_snapshot",
    "browser_click", "browser_take_screenshot",
})
COMMAND_LABELS = {
    "mfs_search.py": "Searching connected knowledge…",
    "mfs_cat.py": "Reading connected knowledge…",
    "mfs_ls.py": "Browsing connected knowledge…",
    "slack_canvas.py": "Creating a Slack canvas…",
    "slack_post_message.py": "Posting to Slack…",
}
DOCUMENT_HELPER_RE = re.compile(
    r"(?:create|generate|render|build|export)[-_].*(?:docx|document|pdf|pptx|xlsx)"
    r"|(?:docx|document|pdf|pptx|xlsx)[-_].*(?:create|generate|render|build|export)",
    re.IGNORECASE,
)
FILE_READ_COMMANDS = frozenset({"cat", "head", "tail"})
FILE_WRITE_COMMANDS = frozenset({"cp", "install", "mkdir", "mv", "tee", "touch"})
TEST_COMMANDS = frozenset({
    "cargo", "go", "jest", "mocha", "npm", "pnpm", "pytest", "swift", "vitest", "yarn",
})


def command_activity_label(command: Any, depth: int = 0) -> str:
    """Recognize a simple invocation, not a helper name mentioned in arguments.

    This is conservative classification, not a shell interpreter or a claim
    that the operation succeeded. Compound commands retain a generic label.
    """
    fallback = "Running a command…"
    if not isinstance(command, str) or len(command) > 8192 or depth > 1:
        return fallback
    try:
        argv = shlex.split(command)
    except ValueError:
        return fallback
    if not argv:
        return fallback
    executable = argv[0].replace("\\", "/").rsplit("/", 1)[-1]
    if executable in {"sh", "bash", "zsh"} and len(argv) == 3 and argv[1] in {"-c", "-lc"}:
        return command_activity_label(argv[2], depth + 1)
    if any(char in command for char in "\n\r;&|<>`$"):
        return fallback
    if re.fullmatch(r"python(?:3(?:\.\d+)?)?(?:\.exe)?", executable):
        if len(argv) >= 3 and argv[1] == "-m" and argv[2] in {"pytest", "unittest"}:
            return "Running tests…"
        if len(argv) < 2 or argv[1].startswith("-"):
            return fallback
        executable = argv[1].replace("\\", "/").rsplit("/", 1)[-1]
    if executable in COMMAND_LABELS:
        return COMMAND_LABELS[executable]
    if DOCUMENT_HELPER_RE.search(executable):
        return "Creating a document…"
    if executable in FILE_READ_COMMANDS:
        return "Reading files…"
    if executable in FILE_WRITE_COMMANDS:
        return "Writing files…"
    if executable in TEST_COMMANDS:
        if executable in {"cargo", "go", "npm", "pnpm", "swift", "yarn"}:
            if len(argv) < 2 or argv[1] not in {"test", "t"}:
                return fallback
        return "Running tests…"
    return fallback


def mcp_activity_label(item: dict[str, Any]) -> str:
    """Expose only explicitly approved service/tool names, with safe fallbacks."""
    server, tool = item.get("server"), item.get("tool")
    service = MCP_SERVICE_NAMES.get(server.lower()) if isinstance(server, str) else None
    name = tool if isinstance(tool, str) and tool in MCP_TOOL_NAMES else None
    if name:
        words = name.replace("-", "_").split("_")
        verb = {"search": "Searching", "fetch": "Fetching", "get": "Reading",
                "read": "Reading", "list": "Listing", "create": "Creating",
                "update": "Updating", "query": "Querying", "resolve": "Looking up"}.get(words[0])
        if verb:
            subject = " ".join(words[1:])
            target = " ".join(part for part in (service, subject) if part)
            return f"{verb} {target or 'with a connected tool'}…"
    if service:
        return f"Using {service}…"
    return "Using a connected tool…"


def activity_label(item: dict[str, Any]) -> str | None:
    """Return a truthful, sanitized label derived from an identifiable action."""
    item_type = item.get("type")
    if item_type == "webSearch":
        return "Searching the web…"
    if item_type == "fileChange":
        return "Updating files…"
    if item_type == "imageView":
        return "Inspecting an image…"
    if item_type == "imageGeneration":
        return "Creating an image…"
    if item_type == "dynamicToolCall":
        return "Using agent tools…"
    if item_type == "mcpToolCall":
        return mcp_activity_label(item)
    if item_type == "commandExecution":
        return command_activity_label(item.get("command"))
    return None
