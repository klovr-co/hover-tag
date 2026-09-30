"""Readable activity titles from bounded tool identities, without model calls.

Inputs are short identities from tag_activity_details, never raw command lines.
Keep unfamiliar tools explicit rather than guessing what they accomplish.

Design reference: T3 Code's separation of command identity and presentation:
https://github.com/pingdotgg/t3code/blob/main/packages/client-runtime/src/work-log/commandLabel.ts
https://github.com/pingdotgg/t3code/blob/main/packages/shared/src/toolActivity.ts
This is an independent Python implementation for Tag's compact Slack rows.
"""
from __future__ import annotations

import re


COMMAND_TITLES = {
    "Tests": "Running tests",
    "ls": "Listing files",
    "find": "Finding files",
    "rg": "Searching text",
    "grep": "Searching text",
    "sed": "Processing text",
    "awk": "Processing text",
    "jq": "Processing JSON",
    "wc": "Counting content",
    "du": "Checking disk usage",
    "file": "Checking file type",
    "stat": "Inspecting file metadata",
    "pytest": "Running tests",
    "unittest": "Running tests",
    "jest": "Running tests",
    "vitest": "Running tests",
    "tsc": "Checking types",
    "eslint": "Checking code",
}


def _running_title(tool: str, fallback: str) -> str:
    """Format an already redacted tool identity for a compact live row."""
    tool = " ".join(tool.split())
    tool = re.sub(r"(?<![\w./-])([A-Za-z0-9_][A-Za-z0-9_.-]*)/SKILL\.md(?=$|[ ,])", r"\1 skill", tool)
    if not tool or tool == "Command":
        return fallback.rstrip("…")
    if re.search(r" (?:&&|\|\||\|) ", tool):
        parts = re.split(r" (&&|\|\||\|) ", tool)
        separators = {"&&": "; ", "||": " or ", "|": " → "}
        return "".join(separators[part] if index % 2 else _running_title(part, fallback)
                       for index, part in enumerate(parts))
    if tool.startswith("Image view · "):
        return "Viewing " + tool.removeprefix("Image view · ")
    if tool.startswith("HTTP "):
        request, _, destination = tool.partition(" · ")
        method = request.removeprefix("HTTP ")
        if method == "GET":
            return ("Downloading " if " → " in destination else "Fetching ") + destination
        if method == "HEAD":
            return "Checking headers for " + destination
        return f"Sending {method} to {destination}"
    for kind, verb in (("change", "Updating"), ("create", "Creating"), ("delete", "Deleting"), ("move", "Moving")):
        prefix = f"File {kind} · "
        if tool.startswith(prefix):
            return verb + " " + tool.removeprefix(prefix)
    if tool in {"File change", "Web search", "Image view"}:
        return {"File change": "Updating files", "Web search": "Searching the web",
                "Image view": "Viewing an image"}[tool]

    if tool.startswith("Package script · "):
        return "Running script " + tool.removeprefix("Package script · ")
    if " · " in tool and not re.match(r"python[\d.]* \(inline\)", tool):
        base, _, targets = tool.partition(" · ")
        return _running_title(base, fallback) + " · " + targets
    program, _, target = tool.partition(" ")
    if target == "--help":
        return f"Reading {program} help"
    if target == "--version":
        return f"Checking {program} version"
    if program == "rg" and target == "--files":
        return "Listing files"
    if program in {"cat", "head", "tail"}:
        if not target:
            return "Reading files"
        verb = {"cat": "Reading", "head": "Reading start of", "tail": "Reading end of"}[program]
        return f"{verb} {target}"
    if program == "git" and target in GIT_TITLES:
        return GIT_TITLES[target]
    if program in {"npm", "pnpm", "yarn", "bun", "cargo", "go", "make", "ruff"}:
        if target in {"test", "t"}:
            return "Running tests"
        if target == "build":
            return "Building the project"
        if target in {"lint", "check"}:
            return "Checking code"
        if target in {"typecheck", "type-check"}:
            return "Checking types"
        if target in {"format", "fmt"}:
            return "Formatting code"
        if target in {"install", "ci"}:
            return "Installing dependencies"
    if program in {"cp", "mv", "mkdir", "rm", "touch"}:
        verb = {"cp": "Copying to", "mv": "Moving to", "mkdir": "Creating folder",
                "rm": "Deleting", "touch": "Updating"}[program]
        return f"{verb} {target}" if target else f"Running {program}"
    if program in COMMAND_TITLES:
        return COMMAND_TITLES[program]
    python = re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", program)
    if python or program in {"node", "ruby", "perl"}:
        language = "Python" if python else {"node": "JavaScript", "ruby": "Ruby", "perl": "Perl"}[program]
        if target in {"-m pytest", "-m unittest"} and python:
            return "Running tests"
        if target.startswith("(inline) · "):
            return f"Running {language} · " + target.removeprefix("(inline) · ")
        if target == "(inline)":
            return f"Running {language} code"
        if target.startswith("-m "):
            return f"Running {target[3:]}"
        return f"Running {target}" if target else f"Running {language}"
    if "/" in program:
        return _connector_title(tool)
    return f"Running {tool}"


GIT_TITLES = {
    "status": "Checking repository status", "diff": "Reviewing changes",
    "log": "Reviewing commit history", "show": "Reviewing Git content",
    "add": "Staging changes", "commit": "Committing changes",
    "fetch": "Fetching remote changes", "pull": "Pulling remote changes",
    "push": "Pushing changes", "checkout": "Checking out Git content",
    "switch": "Switching branches", "restore": "Restoring files",
    "reset": "Resetting Git state", "merge": "Merging changes",
    "rebase": "Rebasing changes", "ls-files": "Listing tracked files",
    "rev-parse": "Checking Git references",
}
SERVICES = {"gmail": "Gmail", "slack": "Slack", "github": "GitHub", "notion": "Notion",
            "google_drive": "Google Drive", "gdrive": "Google Drive",
            "google_calendar": "Google Calendar", "calendar": "Calendar"}
TOOL_VERBS = {"search": "Searching", "list": "Listing", "read": "Reading", "get": "Reading",
              "fetch": "Fetching", "create": "Creating", "update": "Updating", "delete": "Deleting",
              "send": "Sending", "post": "Posting", "query": "Querying",
              "download": "Downloading", "upload": "Uploading"}
PAST_VERBS = {
    "Reading": "Read", "Running": "Ran", "Updating": "Updated", "Listing": "Listed",
    "Finding": "Looked for", "Searching": "Searched", "Checking": "Checked",
    "Reviewing": "Reviewed", "Installing": "Installed", "Creating": "Created",
    "Copying": "Copied", "Moving": "Moved", "Deleting": "Deleted", "Formatting": "Formatted",
    "Fetching": "Fetched", "Pulling": "Pulled", "Pushing": "Pushed", "Staging": "Staged",
    "Committing": "Committed", "Switching": "Switched", "Restoring": "Restored",
    "Resetting": "Reset", "Merging": "Merged", "Rebasing": "Rebased", "Building": "Built",
    "Counting": "Counted", "Processing": "Processed", "Viewing": "Viewed", "Using": "Used", "Sending": "Sent",
    "Posting": "Posted", "Querying": "Queried", "Downloading": "Downloaded", "Uploading": "Uploaded",
    "Inspecting": "Inspected", "Looking": "Looked", "Writing": "Wrote",
}


def _connector_title(tool: str) -> str:
    server, _, name = tool.partition("/")
    service = SERVICES.get(server.lower())
    normalized = name.lower().replace("-", "_")
    for prefix, display in sorted(SERVICES.items(), key=lambda pair: -len(pair[0])):
        if normalized.startswith(prefix + "_"):
            service = display
            normalized = normalized[len(prefix) + 1:]
            break
    verb, _, subject = normalized.partition("_")
    if service and verb in TOOL_VERBS:
        subject = subject.replace("_", " ").strip()
        return " ".join(part for part in (TOOL_VERBS[verb], service, subject) if part)
    return f"Using {tool}"


def activity_title_for_status(title: str, status: str, *, compound: bool = False) -> str:
    """Use past tense only for observed completion, never for failed/unfinished work."""
    if title.startswith("More steps · "):
        return "More steps · " + activity_title_for_status(title.removeprefix("More steps · "), status, compound=compound)
    if status in {"running", "in_progress"}:
        return title
    if status != "completed":
        prefix = {"failed": "Failed", "declined": "Declined", "interrupted": "Stopped"}.get(status, "Incomplete")
        return f"{prefix} · {title}"
    if compound:
        return f"Finished · {title}"
    verb, separator, subject = title.partition(" ")
    return PAST_VERBS.get(verb, verb) + separator + subject


def readable_activity_title(tool: str, fallback: str, status: str = "running") -> str:
    return activity_title_for_status(_running_title(tool, fallback), status,
                                     compound=bool(re.search(r" (?:&&|\|\||\|) ", tool)))
