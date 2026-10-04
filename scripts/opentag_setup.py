#!/usr/bin/env python3
"""Create a private Tag configuration without storing secrets in Git."""

from __future__ import annotations

import argparse
import colorsys
import getpass
import hashlib
import json
import os
import random
import re
import shlex
import shutil
import struct
import subprocess
import sys
import webbrowser
import zlib
from dataclasses import dataclass
from pathlib import Path

try:
    from . import tag_dependencies
except ImportError:
    import tag_dependencies


ROOT = Path(__file__).resolve().parents[1]

try:
    from tag_paths import (
        instance_home,
        initialize_instance,
        initialize_workspace,
        tag_home,
        workspace_home,
    )
    import agent_models
    import tag_config as settings
    import slack_channels
    import setup_ui as ui
    import slack_permissions
    import slack_app_create
    import slack_manifest_migrations
    import slack_credentials
    import slack_identity
    import slack_setup_icons
    import tag_ai
    import tag_instances
    import tag_credentials
    import tag_telemetry
    import tag_cli as lifecycle
    from tag_mascot import PALETTE as MASCOT_PALETTE, PIXELS as MASCOT_PIXELS
except ImportError:
    from scripts.tag_paths import (
        instance_home,
        initialize_instance,
        initialize_workspace,
        tag_home,
        workspace_home,
    )
    from scripts import agent_models
    from scripts import slack_channels, tag_config as settings
    from scripts import setup_ui as ui
    from scripts import slack_permissions
    from scripts import slack_app_create
    from scripts import slack_manifest_migrations
    from scripts import slack_credentials
    from scripts import slack_identity
    from scripts import slack_setup_icons
    from scripts import tag_ai
    from scripts import tag_instances
    from scripts import tag_credentials
    from scripts import tag_telemetry
    from scripts import tag_cli as lifecycle
    from scripts.tag_mascot import PALETTE as MASCOT_PALETTE, PIXELS as MASCOT_PIXELS


REQUIRED_APP_SETTINGS = {
    "Socket Mode": "socket_mode_enabled: true",
    "app mentions": "app_mention",
    "App Home event (app_home_opened)": "app_home_opened",
    "agent stop event": "agent_session_stopped",
    "Home tab enabled": "home_tab_enabled: true",
    "Messages tab enabled": "messages_tab_enabled: true",
    "Agent view enabled": "agent_view:",
    "Interactive controls enabled": "is_enabled: true",
    "mention scope": "app_mentions:read",
    "assistant status scope": "assistant:write",
    "public channel list": "channels:read",
    "public channel join": "channels:join",
    "public history": "channels:history",
    "private channel list": "groups:read",
    "private history": "groups:history",
    "replies": "chat:write",
    "canvas writing": "canvases:write",
    "file access": "files:read",
    "file delivery": "files:write",
    "direct-message event": "message.im",
    "direct-message history": "im:history",
}


def runtime_requirement(package: str) -> str:
    requirements = ROOT / "requirements-runtime.txt"
    for raw_line in requirements.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        distribution = line.split("==", 1)[0].split("[", 1)[0]
        if distribution == package:
            return line
    raise RuntimeError(f"{requirements} does not pin {package}")


def ask(prompt: str, default: str | None = None, *, qid: str | None = None) -> str:
    if ui.protocol_active():
        return ui.text(prompt, default, qid=qid)
    suffix = f" [{default}]" if default else ""
    value = input(f"  {prompt}{suffix}: ").strip()
    return value or (default or "")


def ask_required(prompt: str) -> str:
    while True:
        value = ask(prompt)
        if value:
            return value
        ui.message("A value is required.")


def ensure_agent(config_path: Path, values: dict[str, str], *, review: bool = False) -> dict[str, str]:
    """The AI step: connect Codex or Claude and choose the Tag's default model.

    A saved default whose agent is still connected is kept without asking
    again, unless the person is reviewing setup or went Back to this step.
    """
    saved = values.get("OPENTAG_DEFAULT_MODEL", "")
    returning = ui.going_back_to("ai_connection") or ui.going_back_to("default_model")
    if saved and not review and not returning:
        backend, _ = agent_models.parse_model_choice(saved, values.get("OPENTAG_BACKEND") or "codex")
        if tag_ai.connection(instance_home(), backend)["state"] == "connected":
            return values
    return tag_ai.setup_step(instance_home(), config_path)


def ask_secret(prompt: str, prefix: str, *, qid: str | None = None) -> str:
    while True:
        value = read_secret(prompt, qid=qid)
        if value.startswith(prefix):
            return value
        ui.message(f"Enter the {prefix} token issued by Slack.")


def read_secret(prompt: str, *, qid: str | None = None) -> str:
    if ui.protocol_active():
        return ui.text(prompt, secret=True, qid=qid)
    return getpass.getpass(f"  {prompt}: ").strip()


def confirm(prompt: str, default: bool = True, *, qid: str | None = None) -> bool:
    if ui.protocol_active():
        return ui.confirm(prompt, default, qid=qid)
    choice = "Y/n" if default else "y/N"
    answer = input(f"  {prompt} [{choice}]: ").strip().lower()
    return default if not answer else answer in {"y", "yes"}


TOKEN_RE = re.compile(r"xox(?:a|b|p|s|r)?-[A-Za-z0-9-]+|xapp-[A-Za-z0-9-]+")


def safe_cli_output(value: str) -> str:
    return TOKEN_RE.sub("[redacted Slack token]", value)


def run_slack_cli(arguments: list[str], *, cwd: Path | None = None, interactive: bool = False, quiet: bool = False) -> int:
    command = [shutil.which("slack") or "slack", *arguments, "--skip-update"]
    environment = None
    if cwd:
        icons = sorted((cwd / "assets").glob("tag-profile.*"))
        if icons:
            environment = dict(os.environ, SLACK_CLI_APP_ICON_PATH=str(icons[0]))
    if interactive and ui.protocol_active():
        # Keep Slack CLI's own prompts and output off the protocol stream.
        return subprocess.run(command, cwd=cwd, env=environment, check=False,
                              stdin=subprocess.DEVNULL, stdout=sys.stderr, stderr=sys.stderr).returncode
    if interactive:
        return subprocess.run(command, cwd=cwd, env=environment, check=False).returncode
    completed = subprocess.run(
        command, cwd=cwd, env=environment, check=False, text=True, capture_output=True
    )
    output = safe_cli_output("\n".join(part for part in (completed.stdout, completed.stderr) if part).strip())
    if output and (not quiet or completed.returncode):
        ui.message(output)
    return completed.returncode


def remote_app_settings(project: Path, app_id: str) -> tuple[bool, str]:
    """The app's remote manifest as text, or why Slack CLI couldn't read it."""
    command = [
        shutil.which("slack") or "slack", "manifest", "info", "--source", "remote",
        "--app", app_id, "--skip-update", "--no-color",
    ]
    try:
        completed = subprocess.run(command, cwd=project, check=False, text=True, capture_output=True,
                                   stdin=subprocess.DEVNULL, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return False, "Slack CLI did not respond."
    output = safe_cli_output(completed.stdout or "")
    if completed.returncode == 0 and output:
        return True, output
    return False, safe_cli_output((completed.stderr or completed.stdout or "").strip())


def missing_app_settings(manifest: str, *, enterprise: bool = False) -> list[str]:
    """Labels of Tag's required settings the manifest text doesn't have."""
    normalized = manifest.replace('"', "").replace("'", "")
    required = dict(REQUIRED_APP_SETTINGS)
    if enterprise:
        required["Organization deployment"] = "org_deploy_enabled: true"
    return [
        label for label, marker in required.items()
        if not re.search(rf"(?<![A-Za-z0-9_:]){re.escape(marker)}(?![A-Za-z0-9_:])", normalized)
    ]


EVENT_SETTINGS = ("app mentions", "App Home event (app_home_opened)", "agent stop event", "direct-message event")
APP_HOME_SETTINGS = ("Home tab enabled", "Messages tab enabled")


def is_scope_setting(label: str) -> bool:
    return bool(re.fullmatch(r"[a-z_]+:[a-z_.]+", REQUIRED_APP_SETTINGS.get(label, "")))


def app_check_rows(missing: list[str], *, enterprise: bool = False) -> list[dict[str, object]]:
    """Tag's requirements as ticks, naming what's missing."""
    def row(label: str, labels: tuple[str, ...], detail) -> dict[str, object]:
        absent = [item for item in labels if item in missing]
        return {"label": label, "ok": not absent, "detail": detail(absent) if absent else None}

    markers = lambda absent: ", ".join(REQUIRED_APP_SETTINGS[item] for item in absent)  # noqa: E731
    rows = [
        row("Socket Mode", ("Socket Mode",), lambda _: "Turn on Socket Mode"),
        row("Events", EVENT_SETTINGS, markers),
        row("App Home", APP_HOME_SETTINGS,
            lambda absent: ", ".join("Home tab" if item.startswith("Home") else "Messages tab" for item in absent)),
        row("Agent messaging", ("Agent view enabled",), lambda _: "Turn on Agents & AI Apps"),
        row("Interactivity", ("Interactive controls enabled",), lambda _: "Turn on Interactivity"),
    ]
    scopes = [REQUIRED_APP_SETTINGS[label] for label in missing if is_scope_setting(label)]
    rows.append({"label": f"Missing {len(scopes)} permission{'s' if len(scopes) != 1 else ''}" if scopes
                 else "Permissions", "ok": not scopes, "detail": ", ".join(scopes) or None})
    if enterprise:
        rows.append(row("Organization deployment", ("Organization deployment",),
                        lambda _: "Turn on organization-wide deployment"))
    return rows


def print_manual_steps(missing: list[str]) -> None:
    """The api.slack.com steps for settings Tag's app still needs."""
    ui.message("In Slack app settings:")
    if any(label in missing for label in EVENT_SETTINGS):
        ui.message("Event Subscriptions → Subscribe to bot events:", indent="    ")
        if "app mentions" in missing:
            ui.message("Add app_mention to receive mentions.", indent="      ")
        if "App Home event (app_home_opened)" in missing:
            ui.message("Add app_home_opened to show Tag's Home tab controls.", indent="      ")
        if "agent stop event" in missing:
            ui.message("Add agent_session_stopped to enable the native Stop button.", indent="      ")
        if "direct-message event" in missing:
            ui.message("Add message.im to receive direct messages.", indent="      ")
    if "Home tab enabled" in missing:
        ui.message("App Home → Show Tabs → enable Home Tab.", indent="    ")
    if "Messages tab enabled" in missing:
        ui.message("App Home → Show Tabs → enable Messages Tab.", indent="    ")
    if "Agent view enabled" in missing:
        ui.message("Agents & AI Apps → turn on the agent experience.", indent="    ")
    if "Interactive controls enabled" in missing:
        ui.message("Interactivity & Shortcuts → enable Interactivity.", indent="    ")
    if "Socket Mode" in missing:
        ui.message("Socket Mode → enable Socket Mode.", indent="    ")
    if "Organization deployment" in missing:
        ui.message("Org Level Apps → opt in to organization-wide deployment.", indent="    ")
    scopes = [REQUIRED_APP_SETTINGS[label] for label in missing if is_scope_setting(label)]
    if scopes:
        ui.message("OAuth & Permissions → add bot scopes: " + ", ".join(scopes), indent="    ")
        ui.message("Reinstall the app after adding scopes; Slack may require administrator approval.", indent="    ")
    ui.message("Keep existing settings, then save your changes.")


def inspect_slack_app(project: Path, app_id: str, *, issues: list[str] | None = None) -> bool:
    """Inspect remote settings without changing them; fall back to guided review."""
    if issues is not None:
        issues.clear()
    readable, output = remote_app_settings(project, app_id)
    if readable:
        missing = missing_app_settings(output)
        if not missing:
            ui.message("✓ Existing app has Tag's required Socket Mode, events, and bot scopes")
            return True
        if issues is not None:
            issues.extend(missing)
        manual_missing = [label for label in missing if label != "Agent view enabled"]
        if manual_missing:
            ui.message("App configuration needs attention: " + ", ".join(manual_missing))
            ui.message("Open app settings, make the other listed changes, then choose Check again.")
            print()
            print_manual_steps(manual_missing)
        return False
    if output:
        ui.message(output)
    ui.message("Slack CLI could not inspect the remote manifest with this authorization.")
    return confirm("Have you manually compared the app with Tag's manifest?", default=False, qid="manifest_compared")


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SIGN_IN_AGAIN = "Sign in to Slack again. Tag couldn't confirm which Slack account is setting it up."
ORG_ID_ERROR = "That's the organization ID. Use the workspace's, which starts with T."
NO_WORKSPACE_ID_ERROR = "No workspace ID found. It starts with T."


def slack_sign_ins(output: str) -> list[dict[str, str | None]]:
    """Parse `slack auth list` without reading the CLI's credential files.

    Each sign-in has its Team ID (T… for a workspace, E… for an organization),
    name, and the signed-in member's User ID and handle when the CLI shows them.
    """
    output = _ANSI_RE.sub("", output)
    found: dict[str, dict[str, str | None]] = {}
    current: dict[str, str | None] | None = None
    for line in output.splitlines():
        if "Team ID:" in line:
            match = re.fullmatch(r"\s*(.+?)\s+\(Team ID:\s*([TE][A-Z0-9]+)\)\s*", line)
            current = found.setdefault(match.group(2), {
                "id": match.group(2), "name": match.group(1).strip(),
                "kind": "organization" if match.group(2).startswith("E") else "workspace",
                "user_id": None, "user_name": None,
            }) if match else None
            continue
        if current is None:
            continue
        if match := re.fullmatch(r"\s*User ID:\s*([UW][A-Z0-9]+)\s*", line):
            current["user_id"] = current["user_id"] or match.group(1)
        elif match := re.fullmatch(r"\s*(?:User ?name|User Name|Handle):\s*@?(\S(?:.*\S)?)\s*", line, re.IGNORECASE):
            current["user_name"] = current["user_name"] or match.group(1)
    return list(found.values())


def authorized_accounts(output: str) -> list[tuple[str, str]]:
    return [(str(item["name"]), str(item["id"])) for item in slack_sign_ins(output)]


def authorized_workspaces(output: str) -> list[tuple[str, str]]:
    return [(name, identity) for name, identity in authorized_accounts(output) if identity.startswith("T")]


def list_slack_sign_ins() -> list[dict[str, str | None]]:
    """The Slack CLI's sign-ins on this computer; empty when none can be listed."""
    try:
        result = subprocess.run(
            [shutil.which("slack") or "slack", "auth", "list", "--skip-update", "--no-color"],
            check=False, text=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return slack_sign_ins(result.stdout + "\n" + result.stderr) if result.returncode == 0 else []


def auth_test_member(team_id: str) -> str:
    """Ask Slack who is signed in, through the Slack CLI's own authorization."""
    try:
        result = subprocess.run(
            [shutil.which("slack") or "slack", "api", "auth.test", "--team", team_id,
             "--skip-update", "--no-color"],
            check=False, text=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=15,
        )
        output = result.stdout + "\n" + result.stderr
        start, end = output.find("{"), output.rfind("}")
        payload = json.loads(output[start:end + 1]) if start >= 0 and end > start else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return ""
    if (result.returncode or not isinstance(payload, dict) or not payload.get("ok") or payload.get("bot_id")
            or team_id not in {payload.get("team_id"), payload.get("enterprise_id")}):
        return ""
    user = payload.get("user_id")
    return user if isinstance(user, str) and re.fullmatch(r"[UW][A-Z0-9]+", user) else ""


def signed_in_member(sign_in_id: str, sign_ins: list[dict[str, str | None]] | None = None) -> str:
    """The owner is always the signed-in account; Tag never guesses or offers a list."""
    for item in list_slack_sign_ins() if sign_ins is None else sign_ins:
        if item["id"] == sign_in_id and item["user_id"]:
            return str(item["user_id"])
    if member := auth_test_member(sign_in_id):
        return member
    raise RuntimeError(SIGN_IN_AGAIN)


def ensure_owner(config_path: Path, values: dict[str, str], *, sign_in_id: str = "",
                 sign_ins: list[dict[str, str | None]] | None = None) -> dict[str, str]:
    """Make the person setting up Tag its owner. Saved owners are kept as they are."""
    if not settings.validation_error("SLACK_ALLOWED_USER_IDS", values.get("SLACK_ALLOWED_USER_IDS", "")):
        return values
    owner = signed_in_member(sign_in_id or slack_identity.cli_team(values), sign_ins)
    ui.message(f"Owner: your Slack account ({owner}). Only you can ask this Tag to work.")
    return settings.update_config(config_path, {"SLACK_ALLOWED_USER_IDS": owner})


def parse_workspace_id(value: str) -> tuple[str, str | None]:
    """A workspace ID from a T… ID or an address such as app.slack.com/client/T…."""
    text = value.strip()
    if match := re.search(r"(?<![A-Za-z0-9])(T[A-Z0-9]{2,})(?![A-Za-z0-9])", text):
        return match.group(1), None
    if re.search(r"(?<![A-Za-z0-9])E[A-Z0-9]{2,}(?![A-Za-z0-9])", text):
        return "", ORG_ID_ERROR
    return "", NO_WORKSPACE_ID_ERROR


def ask_text(prompt: str, *, qid: str, default: str = "", error: str | None = None, **details) -> str:
    """A text question that can show why the last answer didn't work."""
    if ui.protocol_active():
        answer = ui.ask_client("text", prompt, qid=qid, default=default, error=error, **details)
        if not isinstance(answer, str):
            raise RuntimeError("The setup client sent a non-text answer")
        return answer.strip()
    if error:
        ui.message(error)
    return ask(prompt, default or None, qid=qid).strip()


def organization_workspaces(enterprise_id: str) -> list[dict[str, str]]:
    """Workspaces in an organization that Tag can offer to pick from.

    Spike result (Slack CLI 4.8): the CLI lists an organization's workspaces
    only inside its own prompts while installing or creating an app, and
    `slack api auth.teams.list --team E…` runs without the sign-in's token
    (`not_authed`). Calling Slack directly would mean reading the CLI's private
    credential file, which Tag doesn't do. So there is no safe list yet, and
    setup offers only "Workspace not listed?". Swap this function when the CLI
    can print the list on its own.
    """
    return []


def choose_org_workspace(organization: dict[str, str | None]) -> slack_identity.WorkspaceSelection:
    enterprise_id, org_name = str(organization["id"]), str(organization["name"])
    workspaces = organization_workspaces(enterprise_id)
    if not ui.protocol_active():
        ui.message(f"{org_name} is an organization. Pick the one workspace this Tag works in.")
        ui.message("To find its ID, open the workspace in Slack in a browser: the address has app.slack.com/client/T….")
        ui.message("An organization admin may need to approve the app. Setup waits and resumes.")
    options = [item["name"] for item in workspaces] + ["Workspace not listed?"]
    ids = [item["id"] for item in workspaces] + ["manual"]
    index = ui.choose(f"Which workspace in {org_name}?", options, qid="org_workspace", option_ids=ids,
                      workspaces=workspaces, organization={"id": enterprise_id, "name": org_name})
    if index < len(workspaces):
        chosen = workspaces[index]
        return slack_identity.WorkspaceSelection(chosen["id"], chosen["name"], enterprise_id, org_name)
    error = None
    while True:
        team_id, error = parse_workspace_id(ask_text("Workspace address or ID (T…)", qid="org_workspace_id",
                                                     error=error))
        if team_id and not (error := settings.validation_error("SLACK_TEAM_ID", team_id)):
            return slack_identity.WorkspaceSelection(team_id, team_id, enterprise_id, org_name)


def slack_sign_in() -> bool:
    """Sign in to Slack through the Slack CLI: the ticket question, or its own prompts in a terminal."""
    if ui.protocol_active():
        return slack_login_with_client()
    if run_slack_cli(["auth", "login"], interactive=True):
        ui.message("Slack CLI authorization was not completed. Run tag setup to try again.")
        return False
    return True


def connect_slack_workspace(current: str = "") -> slack_identity.WorkspaceSelection | None:
    """Pick a workspace from the Slack CLI's sign-ins; sign in only when needed or asked."""
    tag_dependencies.activate_slack(tag_dependencies.ensure_slack(tag_home()))
    while True:
        sign_ins = list_slack_sign_ins()
        if not sign_ins:
            ui.message("Sign in to Slack to choose a workspace.")
            if not slack_sign_in():
                ui.message("Slack sign-in was not completed. Try again.")
                return None
            sign_ins = list_slack_sign_ins()
            if not sign_ins:
                ui.message("No Slack sign-ins could be listed. Check the Slack CLI, then run tag setup again.")
                return None
        options = [str(item["name"]) + (" (organization)" if item["kind"] == "organization" else "")
                   for item in sign_ins] + ["Sign in to another workspace", "Save and exit"]
        ids = [str(item["id"]) for item in sign_ins] + ["sign_in", "exit"]
        default = ids.index(current) if current in ids[:len(sign_ins)] else 0
        index = ui.choose("Which workspace?", options, qid="workspace", option_ids=ids, default=default,
                          workspaces=sign_ins)
        if ids[index] == "exit":
            raise ui.Paused()
        if index == len(sign_ins):
            if not slack_sign_in():
                ui.message("Slack sign-in was not completed. Choose a workspace or try again.")
            continue
        chosen = sign_ins[index]
        user = {"user_id": str(chosen["user_id"] or ""), "user_name": str(chosen["user_name"] or "")}
        if chosen["kind"] == "organization":
            selection = choose_org_workspace(chosen)
            return selection._replace(sign_in_id=str(chosen["id"]), **user)
        ui.message(f"✓ {chosen['name']}")
        return slack_identity.WorkspaceSelection(str(chosen["id"]), str(chosen["name"]),
                                                 sign_in_id=str(chosen["id"]), **user)


# Settings a question saves, cleared when the operator goes back to change it.
OWNER_AND_WORKSPACE = ("SLACK_TEAM_ID", "SLACK_ENTERPRISE_ID", "SLACK_ALLOWED_USER_IDS")
BACK_CLEARS: dict[str, tuple[str, ...]] = {
    "workspace": OWNER_AND_WORKSPACE,
    "org_workspace": OWNER_AND_WORKSPACE,
    "org_workspace_id": OWNER_AND_WORKSPACE,
    "existing_app": ("SLACK_APP_ID",),
    "app_id": ("SLACK_APP_ID",),
    "channels": ("SLACK_CHANNEL_IDS",),
}

SLACK_TICKET_LINE = re.compile(r"(?m)^\s*/slackauthticket\s+(\S+)\s*$")


def slack_login_with_client() -> bool:
    """Slack CLI sign-in without a terminal: the client shows the one-time
    ticket line, the operator sends it in Slack, and returns Slack's code."""
    slack = shutil.which("slack") or "slack"
    try:
        started = subprocess.run(
            [slack, "auth", "login", "--no-prompt", "--skip-update", "--no-color"],
            check=False, text=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("Slack CLI could not start sign-in") from None
    match = SLACK_TICKET_LINE.search((started.stdout or "") + "\n" + (started.stderr or ""))
    if not match:
        raise RuntimeError("Slack CLI did not return a sign-in line")
    ticket = match.group(1)
    for _attempt in range(3):
        code = ui.ask_client("slack_login", "Sign in to Slack", sign_in_line=f"/slackauthticket {ticket}", qid="slack_login")
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_-]{4,128}", code.strip()):
            ui.message("That doesn't look like the code Slack shows. Copy it again.")
            continue
        try:
            finished = subprocess.run(
                [slack, "auth", "login", "--ticket", ticket, "--challenge", code.strip(),
                 "--skip-update", "--no-color"],
                check=False, capture_output=True, stdin=subprocess.DEVNULL, timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError("Slack CLI could not finish sign-in") from None
        # Never relay Slack CLI output here: it can echo the ticket or code.
        if finished.returncode == 0:
            ui.commit()  # Signed in to Slack: Back can't undo that.
            return True
        ui.message("Slack didn't accept that code. Check it and try again.")
    return False


def connect_slack_cli(current: str = "", *, config_path: Path | None = None) -> str | None:
    selected = connect_slack_workspace(current)
    if selected and config_path:
        settings.update_config(config_path, {"SLACK_TEAM_ID": selected.team_id,
                                             "SLACK_ENTERPRISE_ID": selected.enterprise_id})
    return selected[0] if selected else None


def ask_validated(prompt: str, key: str, default: str | None = None, *, qid: str | None = None) -> str:
    while True:
        value = ask(prompt, default, qid=qid)
        if not (error := settings.validation_error(key, value)):
            return value
        ui.message(error)


def slack_project(home: Path) -> Path:
    """Create Tag-owned Slack CLI metadata without touching the source checkout."""
    project = home / "integrations/slack-cli"
    (project / ".slack").mkdir(parents=True, exist_ok=True, mode=0o700)
    config = project / ".slack/config.json"
    if not config.exists():
        config.write_text(json.dumps({"manifest": {"source": "remote"}}, indent=2) + "\n", encoding="utf-8")
    # Remote-manifest management needs project metadata but no SDK run hooks:
    # Tag supervises its own bridge rather than using `slack run`.
    hooks = project / ".slack/hooks.json"
    if not hooks.exists():
        hooks.write_text(json.dumps({"hooks": {}}, indent=2) + "\n", encoding="utf-8")
    return project


SUPPORTED_ICON_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif"})
MIN_ICON_DIMENSION = 512
MAX_ICON_DIMENSION = 2000


def _jpeg_dimensions(path: Path) -> tuple[int, int] | None:
    """Read JPEG dimensions without adding an image-processing dependency."""
    size_markers = frozenset({
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    })
    with path.open("rb") as image:
        if image.read(2) != b"\xff\xd8":
            return None
        while True:
            byte = image.read(1)
            if not byte:
                return None
            if byte != b"\xff":
                continue
            while byte == b"\xff":
                byte = image.read(1)
            if not byte or byte[0] in {0xD8, 0xD9}:
                continue
            length_bytes = image.read(2)
            if len(length_bytes) != 2:
                return None
            length = int.from_bytes(length_bytes, "big")
            if length < 2:
                return None
            if byte[0] in size_markers:
                header = image.read(5)
                if len(header) != 5:
                    return None
                return int.from_bytes(header[3:5], "big"), int.from_bytes(header[1:3], "big")
            image.seek(length - 2, os.SEEK_CUR)


def image_dimensions(path: Path) -> tuple[int, int] | None:
    """Return dimensions for the formats accepted by Slack CLI icon upload."""
    with path.open("rb") as image:
        header = image.read(24)
    if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
        return struct.unpack(">II", header[16:24])
    if header[:6] in {b"GIF87a", b"GIF89a"} and len(header) >= 10:
        return struct.unpack("<HH", header[6:10])
    if header.startswith(b"\xff\xd8"):
        return _jpeg_dimensions(path)
    return None


def parse_local_path(value: str) -> Path | None:
    """Accept quoted or backslash-escaped paths pasted by terminal drag-and-drop."""
    if os.name == "nt":
        candidate = value.strip()
        if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in {'"', "'"}:
            candidate = candidate[1:-1]
        return Path(candidate).expanduser() if candidate else None
    try:
        pieces = shlex.split(value)
    except ValueError:
        return None
    if len(pieces) != 1:
        return None
    return Path(pieces[0]).expanduser()


def slack_cli_supports_icon_upload() -> bool:
    """Require the first Slack CLI release with stable non-hosted app icons."""
    try:
        result = subprocess.run(
            [shutil.which("slack") or "slack", "version", "--skip-update", "--no-color"],
            check=False, text=True, capture_output=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    match = re.search(r"\bv(\d+)\.(\d+)(?:\.\d+)?\b", result.stdout + "\n" + result.stderr)
    return result.returncode == 0 and bool(match) and tuple(map(int, match.groups())) >= (4, 7)


def local_first_name() -> str:
    """This computer's account first name: Slack isn't connected when setup names the Tag."""
    local = re.split(r"[\s._-]+", getpass.getuser().strip(), maxsplit=1)[0]
    return local[:1].upper() + local[1:] if local else ""


def suggested_assistant_name() -> str:
    first_name = local_first_name()
    suggestion = f"{first_name}'s Tag" if first_name else settings.DEFAULTS["OPENTAG_BOT_NAME"]
    return suggestion if len(suggestion) <= 35 else settings.DEFAULTS["OPENTAG_BOT_NAME"]


@dataclass(frozen=True)
class WaterdropBody:
    name: str
    hue: float
    saturation: float = 1.0


@dataclass(frozen=True)
class WaterdropBackground:
    name: str
    hue_offset: float
    saturation: float
    value: float


WATERDROP_BODIES = (
    WaterdropBody("metal", 0, .08),
    WaterdropBody("wood", .36, .86),
    WaterdropBody("water", .57),
    WaterdropBody("fire", .01, .92),
    WaterdropBody("soil", .13, .92),
)
DEFAULT_WATERDROP_ELEMENT = "water"
WATERDROP_BACKGROUNDS = (
    WaterdropBackground("mist", 0, .18, .96),
    WaterdropBackground("haze", -.02, .12, .99),
    WaterdropBackground("veil", .02, .08, 1.0),
)
WATERDROP_APPROVED_BACKGROUNDS = {
    element: ("mist", "haze", "veil")
    for element in ("metal", "wood", "water", "fire", "soil")
}
WATERDROP_HIGHLIGHTS = ("glass", "pearl", "glow", "frost")
WATERDROP_SIGNATURES = (
    "clean", "rose-cheeks", "peach-cheeks", "freckles",
    "north-sparkle", "east-sparkle", "west-sparkle", "twin-sparkles",
    "left-bubble", "right-bubbles", "twin-bubbles", "bubble-trail",
    "gold-crown", "side-stripe", "twin-dots", "heart-mark",
)
WATERDROP_BASE_COUNT = len(WATERDROP_BODIES) * 3 * len(WATERDROP_HIGHLIGHTS)
WATERDROP_SIGNATURE_COUNT = len(WATERDROP_SIGNATURES)
WATERDROP_RECIPE_COUNT = WATERDROP_BASE_COUNT * WATERDROP_SIGNATURE_COUNT
WATERDROP_ELEMENT_RECIPE_COUNT = WATERDROP_RECIPE_COUNT // len(WATERDROP_BODIES)


def waterdrop_recipe(seed: str, recipe_index: int | None = None) -> dict[str, object]:
    """Choose one five-element waterdrop with a subtle, deterministic signature."""
    if recipe_index is None:
        recipe_index = int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:8], "big")
    identity_index = recipe_index % WATERDROP_RECIPE_COUNT
    base_index = identity_index % WATERDROP_BASE_COUNT
    signature_index = identity_index // WATERDROP_BASE_COUNT
    body = WATERDROP_BODIES[base_index % len(WATERDROP_BODIES)]
    background_slot = (base_index // len(WATERDROP_BODIES)) % 3
    background_name = WATERDROP_APPROVED_BACKGROUNDS[body.name][background_slot]
    background = next(item for item in WATERDROP_BACKGROUNDS if item.name == background_name)
    highlight = WATERDROP_HIGHLIGHTS[base_index // (len(WATERDROP_BODIES) * 3)]
    return {
        "index": identity_index,
        "base_index": base_index,
        "signature_index": signature_index,
        "body": body,
        "background": background,
        "highlight": highlight,
        "signature": WATERDROP_SIGNATURES[signature_index],
    }


def _waterdrop_assignments(project: Path) -> tuple[Path, dict[str, object]]:
    assets = project / "assets"
    assets.mkdir(parents=True, exist_ok=True, mode=0o700)
    assignments_path = assets / "tag-waterdrop-identities.json"
    try:
        assignments = json.loads(assignments_path.read_text(encoding="utf-8"))
        if not isinstance(assignments, dict):
            assignments = {}
    except (OSError, json.JSONDecodeError):
        assignments = {}
    return assignments_path, assignments


def _remember_waterdrop_index(project: Path, seed: str, identity_index: int) -> None:
    assignments_path, assignments = _waterdrop_assignments(project)
    assignments[seed] = {
        "index": identity_index % WATERDROP_RECIPE_COUNT,
        "element": waterdrop_recipe(seed, identity_index)["body"].name,
    }
    temporary = assignments_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(assignments, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, assignments_path)
    if os.name != "nt":
        assignments_path.chmod(0o600)


def _element_identity_index(element: str, variation_index: int) -> int:
    """Map an element-local variant onto the stable global recipe index."""
    element_index = next(
        (index for index, body in enumerate(WATERDROP_BODIES) if body.name == element),
        None,
    )
    if element_index is None:
        raise ValueError(f"Unknown waterdrop element: {element}")
    variation_index %= WATERDROP_ELEMENT_RECIPE_COUNT
    background_slot = variation_index % 3
    highlight_slot = (variation_index // 3) % len(WATERDROP_HIGHLIGHTS)
    signature_index = variation_index // (3 * len(WATERDROP_HIGHLIGHTS))
    base_index = (
        element_index
        + len(WATERDROP_BODIES) * background_slot
        + len(WATERDROP_BODIES) * 3 * highlight_slot
    )
    return base_index + WATERDROP_BASE_COUNT * signature_index


def other_tag_waterdrops() -> set[int]:
    """Waterdrop identities other Tags on this computer already use."""
    used: set[int] = set()
    try:
        own = instance_home().resolve()
        homes = [Path(str(item["home"])) for item in tag_instances.discover(tag_home()) if item.get("valid")]
    except (OSError, ValueError, RuntimeError):
        return used
    for home in homes:
        try:
            if home.resolve() == own:
                continue
        except OSError:
            continue
        assets = home / "integrations/slack-cli/assets"
        state = _read_json(assets / PICTURE_STATE)
        if state.get("picture") == "waterdrop" and isinstance(state.get("index"), int):
            used.add(state["index"] % WATERDROP_RECIPE_COUNT)
        # Tags set up before the picture record keep their identities here.
        for item in _read_json(assets / "tag-waterdrop-identities.json").values():
            if isinstance(item, dict) and isinstance(item.get("index"), int):
                used.add(item["index"] % WATERDROP_RECIPE_COUNT)
    return used


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _assigned_waterdrop_index(
    project: Path, seed: str, element: str = DEFAULT_WATERDROP_ELEMENT, *, occupied: set[int] | None = None
) -> int:
    """Keep an identity stable for its seed and avoid ones other Tags use."""
    occupied = other_tag_waterdrops() if occupied is None else occupied
    _, assignments = _waterdrop_assignments(project)
    saved = assignments.get(seed)
    if isinstance(saved, dict) and isinstance(saved.get("index"), int):
        saved_index = saved["index"] % WATERDROP_RECIPE_COUNT
        if waterdrop_recipe(seed, saved_index)["body"].name == element and saved_index not in occupied:
            return saved_index
    variation_index = int.from_bytes(
        hashlib.sha256(seed.encode("utf-8")).digest()[:8], "big"
    ) % WATERDROP_ELEMENT_RECIPE_COUNT
    for _ in range(WATERDROP_ELEMENT_RECIPE_COUNT):
        identity_index = _element_identity_index(element, variation_index)
        if identity_index not in occupied:
            break
        variation_index = (variation_index + 1) % WATERDROP_ELEMENT_RECIPE_COUNT
    _remember_waterdrop_index(project, seed, identity_index)
    return identity_index


def shuffled_waterdrop_index(current: int | None, occupied: set[int]) -> int:
    """Any element: a different element or signature from the current picture."""
    now = waterdrop_recipe("", current) if current is not None else None

    def differs(index: int) -> bool:
        recipe = waterdrop_recipe("", index)
        return not now or recipe["body"] != now["body"] or recipe["signature"] != now["signature"]

    candidates = [index for index in range(WATERDROP_RECIPE_COUNT)
                  if index != current and index not in occupied and differs(index)]
    if not candidates:  # Every identity is taken: any other picture will do.
        candidates = [index for index in range(WATERDROP_RECIPE_COUNT) if index != current and differs(index)]
    return random.SystemRandom().choice(candidates)


def _rgb_bytes(hue: float, saturation: float, value: float) -> bytes:
    red, green, blue = colorsys.hsv_to_rgb(hue % 1, max(0, min(1, saturation)), max(0, min(1, value)))
    return bytes(round(channel * 255) for channel in (red, green, blue))


def _body_color(value: str, body: WaterdropBody) -> bytes:
    red, green, blue = (int(value[index:index + 2], 16) / 255 for index in (1, 3, 5))
    original_hue, saturation, brightness = colorsys.rgb_to_hsv(red, green, blue)
    base_hue = colorsys.rgb_to_hsv(0x20 / 255, 0xCA / 255, 0xFE / 255)[0]
    hue_delta = ((original_hue - base_hue + .5) % 1) - .5
    return _rgb_bytes(body.hue + hue_delta * .24, saturation * body.saturation, brightness)


def _waterdrop_palette(recipe: dict[str, object]) -> dict[str, bytes]:
    body = recipe["body"]
    background = recipe["background"]
    assert isinstance(body, WaterdropBody) and isinstance(background, WaterdropBackground)
    background_color = _rgb_bytes(
        body.hue + background.hue_offset,
        background.saturation * body.saturation,
        background.value,
    )
    palette = {
        key: background_color if key in "BCDEFG" else _body_color(value, body)
        for key, value in MASCOT_PALETTE.items()
    }
    highlight = recipe["highlight"]
    if highlight == "glass":
        palette["A"] = bytes((250, 253, 253))
    elif highlight == "pearl":
        palette["A"] = bytes((255, 247, 219))
    elif highlight == "glow":
        palette["A"] = _rgb_bytes(body.hue + .08, .18, 1)
    else:
        palette["A"] = _rgb_bytes(body.hue + .50, .12, .98)
    return palette


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def branded_profile_icon(
    project: Path,
    seed: str,
    *,
    recipe_index: int | None = None,
    element: str = DEFAULT_WATERDROP_ELEMENT,
) -> Path:
    """Render one curated, deterministic Tag waterdrop identity as a Slack icon."""
    width = height = 512
    scale = 12
    sprite_width, sprite_height = len(MASCOT_PIXELS[0]), len(MASCOT_PIXELS)
    left = (width - sprite_width * scale) // 2
    top = (height - sprite_height * scale) // 2
    if recipe_index is None:
        recipe_index = _assigned_waterdrop_index(project, seed, element)
    recipe = waterdrop_recipe(seed, recipe_index)
    palette = _waterdrop_palette(recipe)
    background = palette["E"]
    rows = []
    for y in range(height):
        source_y = (y - top) // scale
        row = bytearray(background * width)
        if 0 <= source_y < sprite_height:
            for source_x, key in enumerate(MASCOT_PIXELS[source_y]):
                start = left + source_x * scale
                row[start * 3:(start + scale) * 3] = palette[key] * scale
        rows.append(row)

    def paint_cell(column: int, row: int, color: bytes, *, cells_wide: int = 1, cells_high: int = 1) -> None:
        x_start, y_start = left + column * scale, top + row * scale
        for pixel_y in range(max(0, y_start), min(height, y_start + cells_high * scale)):
            start = max(0, x_start) * 3
            end = min(width, x_start + cells_wide * scale) * 3
            rows[pixel_y][start:end] = color * ((end - start) // 3)

    body = recipe["body"]
    assert isinstance(body, WaterdropBody)
    signature = recipe["signature"]

    def sparkle(column: int, row: int, color: bytes) -> None:
        """Paint a four-cell-wide mark that survives Slack-size downsampling."""
        paint_cell(column, row - 1, color, cells_wide=2)
        paint_cell(column - 1, row, color, cells_wide=4, cells_high=2)
        paint_cell(column, row + 2, color, cells_wide=2)

    def bubble_mark(column: int, row: int) -> None:
        """Paint a small diamond bubble with a readable white glint."""
        paint_cell(column + 1, row, bubble, cells_wide=2)
        paint_cell(column, row + 1, bubble, cells_wide=4, cells_high=2)
        paint_cell(column + 1, row + 3, bubble, cells_wide=2)
        paint_cell(column + 1, row + 1, palette["A"])

    cheek = _rgb_bytes(body.hue + .38, .48, 1)
    peach = _rgb_bytes(.04, .38, 1)
    bubble = _rgb_bytes(body.hue + .04, .72, .96)
    accent = _rgb_bytes(body.hue + .38, .78, .94)
    gold = _rgb_bytes(.13, .88, 1)
    if signature == "rose-cheeks":
        paint_cell(5, 21, cheek, cells_wide=3, cells_high=2)
        paint_cell(20, 21, cheek, cells_wide=3, cells_high=2)
    elif signature == "peach-cheeks":
        paint_cell(5, 21, peach, cells_wide=3, cells_high=2)
        paint_cell(20, 21, peach, cells_wide=3, cells_high=2)
    elif signature == "freckles":
        for column, row in ((6, 21), (9, 22), (17, 22), (20, 21)):
            paint_cell(column, row, cheek, cells_wide=2)
    elif signature == "north-sparkle":
        sparkle(24, 9, palette["A"])
    elif signature == "east-sparkle":
        sparkle(24, 16, palette["A"])
    elif signature == "west-sparkle":
        sparkle(2, 14, palette["A"])
    elif signature == "twin-sparkles":
        sparkle(2, 14, palette["A"])
        sparkle(24, 9, palette["A"])
    elif signature == "left-bubble":
        bubble_mark(1, 11)
    elif signature == "right-bubbles":
        bubble_mark(23, 13)
    elif signature == "twin-bubbles":
        bubble_mark(1, 12)
        bubble_mark(23, 14)
    elif signature == "bubble-trail":
        bubble_mark(1, 16)
        paint_cell(3, 13, bubble, cells_wide=2, cells_high=2)
        paint_cell(5, 10, bubble, cells_wide=2, cells_high=2)
    elif signature == "gold-crown":
        paint_cell(11, 13, gold, cells_wide=2, cells_high=3)
        paint_cell(14, 12, gold, cells_wide=2, cells_high=4)
        paint_cell(17, 13, gold, cells_wide=2, cells_high=3)
        paint_cell(11, 16, gold, cells_wide=8, cells_high=2)
    elif signature == "side-stripe":
        paint_cell(3, 16, accent, cells_wide=3, cells_high=2)
        paint_cell(4, 18, accent, cells_wide=4, cells_high=2)
        paint_cell(5, 20, accent, cells_wide=4, cells_high=2)
    elif signature == "twin-dots":
        paint_cell(7, 14, gold, cells_wide=3, cells_high=3)
        paint_cell(19, 16, accent, cells_wide=3, cells_high=3)
    elif signature == "heart-mark":
        paint_cell(16, 23, accent, cells_wide=2, cells_high=2)
        paint_cell(19, 23, accent, cells_wide=2, cells_high=2)
        paint_cell(16, 25, accent, cells_wide=5, cells_high=2)
        paint_cell(17, 27, accent, cells_wide=3)
        paint_cell(18, 28, accent)

    filtered_rows = [b"\x00" + bytes(row) for row in rows]
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(b"".join(filtered_rows), level=9))
        + _png_chunk(b"IEND", b"")
    )
    assets = project / "assets"
    assets.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = assets / ".tag-profile.png.tmp"
    destination = assets / "tag-profile.png"
    try:
        temporary.write_bytes(png)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    if os.name != "nt":
        destination.chmod(0o600)
    for previous in assets.glob("tag-profile.*"):
        if previous != destination:
            previous.unlink()
    return destination


def validate_profile_icon(path: Path) -> str | None:
    if not path.is_file():
        return "Choose an existing image file."
    if path.suffix.lower() not in SUPPORTED_ICON_SUFFIXES:
        return "Use a PNG, JPEG, or GIF image."
    try:
        dimensions = image_dimensions(path)
    except OSError:
        dimensions = None
    if dimensions is None:
        return "Use a PNG, JPEG, or GIF image."
    width, height = dimensions
    if not (MIN_ICON_DIMENSION <= width <= MAX_ICON_DIMENSION
            and MIN_ICON_DIMENSION <= height <= MAX_ICON_DIMENSION):
        return f"This picture is {width}×{height}. Use one between 512×512 and 2000×2000 pixels."
    return None


def save_profile_icon(project: Path, source: Path) -> Path:
    """Copy a chosen icon into Tag-owned storage for Slack CLI auto-detection."""
    assets = project / "assets"
    assets.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = assets / f"tag-profile{source.suffix.lower()}"
    temporary = assets / f".tag-profile{source.suffix.lower()}.tmp"
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    if os.name != "nt":
        destination.chmod(0o600)
    for previous in assets.glob("tag-profile.*"):
        if previous != destination:
            previous.unlink()
    return destination


# The picture Tag will upload: kind, label, and the waterdrop identity. Not
# named tag-profile.*, which Slack CLI would take for the picture itself.
PICTURE_STATE = "tag-picture.json"


def profile_picture(project: Path) -> dict | None:
    """The saved picture, when its file is still there."""
    state = _read_json(project / "assets" / PICTURE_STATE)
    preview = Path(str(state.get("preview", "")))
    if state.get("picture") not in {"waterdrop", "custom"} or not preview.is_file() \
            or preview.parent.resolve() != (project / "assets").resolve():
        return None
    return state


def _save_picture(project: Path, state: dict) -> dict:
    settings.save_config(project / "assets" / PICTURE_STATE, state)
    return state


def picture_revision(picture: dict | None) -> str | None:
    """Identify the bytes, since shuffles and uploads reuse tag-profile's path."""
    return hashlib.sha256(Path(picture["preview"]).read_bytes()).hexdigest() if picture else None


def waterdrop_picture(project: Path, seed: str, *, shuffle_from: int | None = None, shuffle: bool = False) -> dict:
    """Render a waterdrop no other Tag on this computer uses, and remember it."""
    occupied = other_tag_waterdrops()
    index = (shuffled_waterdrop_index(shuffle_from, occupied) if shuffle
             else _assigned_waterdrop_index(project, seed, occupied=occupied))
    path = branded_profile_icon(project, seed, recipe_index=index)
    element = waterdrop_recipe(seed, index)["body"].name
    return _save_picture(project, {
        "picture": "waterdrop", "index": index, "element": element,
        "label": f"{element.title()} · Tag waterdrop #{index + 1:04d}", "preview": str(path.resolve()),
    })


def custom_picture(project: Path, source: Path) -> tuple[dict | None, str | None]:
    """Validate and copy a chosen picture; the error says what to change."""
    if error := validate_profile_icon(source):
        return None, error
    if slack_cli_too_old():
        return None, "Your own picture needs Slack CLI 4.7 or newer. Update Slack CLI, then try again."
    saved = save_profile_icon(project, source)
    return _save_picture(project, {"picture": "custom", "label": source.name,
                                   "preview": str(saved.resolve())}), None


def slack_cli_too_old() -> bool:
    """True only for an installed Slack CLI older than 4.7; setup installs it later otherwise."""
    return bool(shutil.which("slack")) and not slack_cli_supports_icon_upload()


def default_profile_name(values: dict[str, str], *, test_mode: bool = False) -> str:
    name = values.get("OPENTAG_BOT_NAME", settings.DEFAULTS["OPENTAG_BOT_NAME"])
    if name == settings.DEFAULTS["OPENTAG_BOT_NAME"]:
        name = suggested_assistant_name()
        if test_mode:
            name = f"TEST · {name}"
            if len(name) > 35:
                name = "TEST · Tag"
    return name


def save_profile(config_path: Path, name: str, description: str) -> str | None:
    """Validate and save the name and description; the error says what to change."""
    if error := settings.validation_error("OPENTAG_BOT_NAME", name):
        return error
    if error := settings.validation_error("OPENTAG_BOT_DESCRIPTION", description):
        return error
    settings.update_config(config_path, {"OPENTAG_BOT_NAME": name, "OPENTAG_BOT_DESCRIPTION": description})
    return None


def choose_profile(project: Path, config_path: Path, *, editing: bool = False, test_mode: bool = False) -> str:
    """Your Tag: name, one-line description, and picture. Returns ``new`` or ``existing``.

    Nothing touches Slack here. ``existing`` (not offered while editing) means
    the person will use an app they already have, which keeps its own name and
    picture.
    """
    values = settings.load_config(config_path)
    name = default_profile_name(values, test_mode=test_mode)
    description = values.get("OPENTAG_BOT_DESCRIPTION", "")
    picture = profile_picture(project) or waterdrop_picture(project, "", shuffle=True)
    if ui.protocol_active():
        error = None
        while True:
            answer = ui.ask_client(
                "profile_picture", "Meet your new Tag", qid="profile", name=name, name_limit=35,
                description=description, description_limit=140, preview=picture["preview"],
                preview_revision=picture_revision(picture),
                picture=picture["picture"], picture_label=picture["label"], error=error,
                editing=editing, can_use_existing=not editing,
            )
            error = None
            if answer == "shuffle":
                ui.forget_last("profile")  # Replaying a shuffle after Back would change the picture.
                picture = waterdrop_picture(project, "", shuffle=True, shuffle_from=picture.get("index"))
                continue
            if answer == "existing" and not editing:
                return "existing"
            if not isinstance(answer, dict):
                raise RuntimeError("The setup client sent an unreadable profile answer")
            if "picture" in answer:
                source = answer["picture"]
                if not isinstance(source, str) or not source.strip():
                    error = "Choose an existing image file."
                else:
                    chosen, error = custom_picture(project, Path(source.strip()).expanduser())
                    picture = chosen or picture
                if "name" not in answer or error:
                    ui.forget_last("profile")
                    continue
            if "name" not in answer:
                raise RuntimeError("The setup client sent an unreadable profile answer")
            name, description = answer.get("name"), answer.get("description", "")
            if not isinstance(name, str) or not isinstance(description, str):
                raise RuntimeError("The setup client sent an unreadable profile answer")
            name, description = name.strip(), description.strip()
            if not (error := save_profile(config_path, name, description)):
                return "new"

    print()
    ui.message("Meet your new Tag. Nothing is created in Slack until you approve it.")
    if test_mode:
        ui.message("TEST MODE · This name will identify a real Slack test app.", code=ui.display.WARNING)
    while True:
        candidate = ask("Name", name, qid="assistant_name").strip()
        if error := settings.validation_error("OPENTAG_BOT_NAME", candidate):
            ui.message(error)
            continue
        name = candidate
        break
    while True:
        candidate = ask("One-line description (optional)", description or None,
                        qid="assistant_description").strip()
        if error := settings.validation_error("OPENTAG_BOT_DESCRIPTION", candidate):
            ui.message(error)
            continue
        description = candidate
        break
    settings.update_config(config_path, {"OPENTAG_BOT_NAME": name, "OPENTAG_BOT_DESCRIPTION": description})
    options = ["Keep this picture", "Shuffle picture", "Choose my own picture", "Open picture preview"]
    options += [] if editing else ["Use an existing app"]
    options += ["Save and exit"]
    while True:
        print()
        ui.display.info_row("Picture", picture["label"])
        action = options[ui.choose("Profile picture", options, qid="profile_picture")]
        if action == "Keep this picture":
            return "new"
        if action == "Use an existing app":
            return "existing"
        if action == "Shuffle picture":
            picture = waterdrop_picture(project, "", shuffle=True, shuffle_from=picture.get("index"))
        elif action == "Choose my own picture":
            ui.message("Drag a picture here, or paste its local path. Enter goes back.")
            ui.message("PNG, JPEG, or GIF · 512–2000 px in each dimension")
            while True:
                raw = ask("Picture path", qid="picture_path")
                if not raw:
                    break
                source = parse_local_path(raw)
                chosen, error = (None, "Enter one local image path.") if source is None else custom_picture(project, source)
                if error:
                    ui.message(error)
                    continue
                picture = chosen
                ui.message(f"✓ Profile picture ready: {source.name}")
                break
        elif action == "Open picture preview":
            try:
                opened = webbrowser.open(Path(picture["preview"]).resolve().as_uri())
            except (OSError, ValueError):
                opened = False
            if not opened:
                ui.message(f"Could not open the image viewer. Preview: {picture['preview']}")


def saved_slack_app(project: Path, team_id: str, app_id: str) -> bool:
    """Reuse Slack CLI's own link records, including links made before Tag checkpoints."""
    saved_ids = slack_app_create.saved_app_ids(project, team_id)
    if app_id in saved_ids:
        return True
    if saved_ids:
        raise RuntimeError("This workspace is already linked to another app (" + ", ".join(sorted(saved_ids)) +
                           "). Select that App ID in Tag settings or use a separate Tag home for the other app. Existing links were kept.")
    return False


def choose_slack_app(
    home: Path, team_id: str, config_path: Path | None = None, *, test_mode: bool = False, progress=None,
) -> str:
    """The new-app path after the Create recap: create the app (or resume its
    creation), then link it and check its settings. A saved App ID from an
    earlier setup is linked and checked the same way."""
    config_path = config_path or settings.config_path(home)
    values = settings.load_config(config_path)
    app_id = values.get("SLACK_APP_ID", "")
    team_id = slack_identity.cli_team(values) or team_id
    project = slack_project(home)
    creation = project / "tag-create.json"
    if creation.exists():
        state = slack_app_create.read_object(creation)
        # An explicitly supplied ID can recover an uncertain creation via normal linking.
        if not app_id or state.get("app_id") == app_id:
            app_id = slack_app_create.create_app(project, team_id, config_path, run_slack_cli,
                                                 approved=True, progress=progress)
    if not app_id:
        # The Create recap was the approval.
        app_id = slack_app_create.create_app(project, team_id, config_path, run_slack_cli,
                                             approved=True, progress=progress)
    ui.message(f"Selected app: {app_id}")
    # Keep link progress separately from credential validation, including across exits.
    marker = project / "tag-linked.json"
    if not saved_slack_app(project, team_id, app_id):
        ui.message("Linking keeps your app's existing permissions.")
        if ui.choose("Continue with this app?", ["Link app and check settings", "Save and exit"], qid="link_app") == 1:
            raise ui.Paused()
        while not saved_slack_app(project, team_id, app_id):
            result = run_slack_cli(
                ["app", "link", "--team", team_id, "--app", app_id, "--environment", "local"], cwd=project, quiet=True,
            )
            if saved_slack_app(project, team_id, app_id):
                break
            if result == 0:
                ui.message("Slack returned success, but the app link could not be confirmed locally.")
            if ui.choose("App linking needs attention", ["Check again", "Save and exit"], qid="link_app_retry") == 1:
                raise ui.Paused()
    settings.save_config(marker, {"app_id": app_id, "team_id": team_id})
    ui.message("✓ App linked")
    if values.get("SLACK_ENTERPRISE_ID"):
        ui.message("Enabling organization deployment for the selected workspace.")
        slack_manifest_migrations.enable_org_deployment(project, app_id, values["SLACK_ENTERPRISE_ID"])
    ui.commit()  # The Slack app exists and is linked: Back stops here.

    def enable_agent_messaging() -> bool:
        def approve_legacy() -> bool:
            ui.notice(
                "Slack currently uses the legacy Assistant messaging experience",
                "Switching this app to Agent messaging cannot be reversed.",
            )
            return confirm("Switch permanently to Agent messaging?", default=False, qid="agent_messaging_switch")

        return slack_manifest_migrations.enable_agent_view(
            project, app_id, team_id, approve_legacy=approve_legacy
        )

    issues: list[str] = []
    while not inspect_slack_app(project, app_id, issues=issues):
        can_enable_agent = "Agent view enabled" in issues
        if can_enable_agent:
            try:
                changed = enable_agent_messaging()
            except RuntimeError as exc:
                ui.message(str(exc))
            else:
                if changed:
                    ui.message("✓ Agent messaging enabled through Slack CLI")
                    continue
                issues.remove("Agent view enabled")
                can_enable_agent = False
                if not issues:
                    break
        print()
        ui.message("Your app selection and link are saved.")
        while True:
            options = (["Retry Agent messaging with Slack CLI"] if can_enable_agent else []) + [
                "Open app settings", "Check again", "Save and exit",
            ]
            choice = ui.choose("App settings need attention", options, qid="app_settings_repair")
            if can_enable_agent and choice == 0:
                try:
                    changed = enable_agent_messaging()
                except RuntimeError as exc:
                    ui.message(str(exc))
                    continue
                if changed:
                    ui.message("✓ Agent messaging enabled through Slack CLI")
                break
            browser_choice = choice - int(can_enable_agent)
            if browser_choice == 0:
                webbrowser.open(f"https://api.slack.com/apps/{app_id}")
            elif browser_choice == 1:
                break
            else:
                raise ui.Paused()
    return app_id


APP_ID_RE = re.compile(r"(?<![A-Za-z0-9])(A[A-Z0-9]{2,})(?![A-Za-z0-9])")


def other_tag_apps() -> dict[str, dict[str, str]]:
    """Apps other Tags on this computer use (``SLACK_APP_ID``).

    One app serves one Tag: two Tags on one app would share its Socket Mode events.
    """
    apps: dict[str, dict[str, str]] = {}
    try:
        own = instance_home().resolve()
        items = tag_instances.discover(tag_home())
    except (OSError, ValueError, RuntimeError):
        return apps
    for item in items:
        home = Path(str(item.get("home", "")))
        try:
            if not item.get("valid") or home.resolve() == own:
                continue
            values = settings.load_config(home / "config/settings.json")
        except (OSError, ValueError):
            continue
        app_id = values.get("SLACK_APP_ID", "")
        if re.fullmatch(r"A[A-Z0-9]+", app_id):
            name = values.get("OPENTAG_BOT_NAME") or app_id
            apps[app_id] = {"name": name, "tag": tag_instances.nickname(home) or name,
                            "team": slack_identity.cli_team(values), "workspace": values.get("SLACK_TEAM_ID", "")}
    return apps


def slack_cli_apps(project: Path, team_id: str) -> dict[str, str]:
    """Apps the Slack CLI lists for Tag's own project (`slack app list`), with names when it shows them."""
    if not any((project / ".slack" / name).is_file() for name in ("apps.json", "apps.dev.json")):
        return {}
    try:
        result = subprocess.run(
            [shutil.which("slack") or "slack", "app", "list", "--team", team_id, "--skip-update", "--no-color"],
            cwd=project, check=False, text=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode:
        return {}
    apps: dict[str, str] = {}
    name = ""
    for line in _ANSI_RE.sub("", result.stdout).splitlines():
        if match := re.fullmatch(r"\s*(?:App Name|Name):\s*(\S.*?)\s*", line):
            name = match.group(1)
        elif match := re.fullmatch(r"\s*(\S.*?)\s+\(App ID:\s*(A[A-Z0-9]+)\)\s*", line):
            apps[match.group(2)] = match.group(1)
            name = ""
        elif match := re.fullmatch(r"\s*App ID:\s*(A[A-Z0-9]+)\s*", line):
            apps[match.group(1)] = name or match.group(1)
            name = ""
    return apps


def known_apps(home: Path, team_id: str, workspace_id: str = "") -> list[dict[str, str | None]]:
    """Apps Tag already knows for this workspace: linked here, listed by the Slack CLI, or used by other Tags."""
    project = slack_project(home)
    used = other_tag_apps()
    apps: dict[str, dict[str, str | None]] = {}
    try:
        linked = slack_app_create.saved_app_ids(project, team_id)
    except RuntimeError:
        linked = set()
    for app_id in sorted(linked):
        apps[app_id] = {"id": app_id, "name": app_id, "source": "linked", "used_by": None}
    for app_id, name in slack_cli_apps(project, team_id).items():
        entry = apps.setdefault(app_id, {"id": app_id, "name": name, "source": "cli", "used_by": None})
        if entry["name"] == app_id:
            entry["name"] = name
    for app_id, other in used.items():
        if other["team"] in {team_id, workspace_id} or other["workspace"] == workspace_id or app_id in apps:
            entry = apps.setdefault(app_id, {"id": app_id, "name": other["name"], "source": "tag", "used_by": None})
            entry["used_by"] = other["tag"]
            if entry["name"] == app_id:
                entry["name"] = other["name"]
    return list(apps.values())


def used_by_message(tag_name: str) -> str:
    return f"Your Tag “{tag_name}” already uses this app. Pick another one."


def ask_app_id(team_id: str) -> str:
    """An app link or ID, for an app Tag doesn't know yet."""
    if not ui.protocol_active():
        print()
        ui.message("Find your app's ID:")
        ui.message("1. Open https://api.slack.com/apps (sign in if asked).")
        ui.message(f"2. Select an app you manage for workspace {team_id}.")
        ui.message("3. Copy its address, or Basic Information → App Credentials → App ID.")
        ui.message("This is not a token or Client ID.")
    used = other_tag_apps()
    error = None
    while True:
        value = ask_text("App link or ID (A…)", qid="app_id", error=error)
        match = APP_ID_RE.search(value)
        if not match:
            error = "No App ID found. It starts with A."
        elif match.group(1) in used:
            error = used_by_message(used[match.group(1)]["tag"])
        else:
            return match.group(1)


def unlink_local(project: Path, team_id: str, app_id: str) -> None:
    """Undo a link setup made in Tag's own Slack CLI project. The app itself is untouched."""
    path = project / ".slack/apps.dev.json"
    try:
        records = slack_app_create.read_object(path) if path.exists() else {}
    except RuntimeError:
        return
    record = records.get(team_id)
    if isinstance(record, dict) and record.get("app_id") == app_id:
        del records[team_id]
        settings.save_config(path, records)
    marker = project / "tag-linked.json"
    if marker.exists() and _read_json(marker).get("app_id") == app_id:
        marker.unlink()


def link_app(home: Path, config_path: Path, team_id: str, app_id: str) -> None:
    """Link the app to Tag's Slack CLI project. Linking changes nothing in the app."""
    project = slack_project(home)
    progress = setup_progress(config_path)
    previous = progress.get("linked_by_setup")
    if previous and previous != app_id:
        unlink_local(project, team_id, previous)
        save_progress(config_path, linked_by_setup=None)
    while not saved_slack_app(project, team_id, app_id):
        save_progress(config_path, linked_by_setup=app_id)
        result = run_slack_cli(
            ["app", "link", "--team", team_id, "--app", app_id, "--environment", "local"], cwd=project, quiet=True,
        )
        if saved_slack_app(project, team_id, app_id):
            break
        if result == 0:
            ui.message("Slack returned success, but the app link could not be confirmed locally.")
        if ui.choose("App linking needs attention", ["Check again", "Save and exit"], qid="link_app_retry") == 1:
            raise ui.Paused()
    settings.save_config(project / "tag-linked.json", {"app_id": app_id, "team_id": team_id})


def update_app_settings(home: Path, team_id: str, app_id: str, enterprise: bool) -> None:
    """Add Tag's missing settings to the app, after the person chose Update app."""
    project = slack_project(home)
    if slack_manifest_migrations.has_legacy_assistant(project, app_id):
        def approve_legacy() -> bool:
            ui.notice("This app uses Slack's legacy Assistant messaging",
                      "Switching it to Agent messaging can't be undone.")
            return confirm("Switch permanently to Agent messaging?", default=False, qid="agent_messaging_switch")

        if not slack_manifest_migrations.enable_agent_view(project, app_id, team_id, approve_legacy=approve_legacy):
            raise RuntimeError("The app still uses legacy Assistant messaging, so it wasn't updated.")
    ui.commit()  # The app in Slack changed: Back can't undo that.
    if slack_manifest_migrations.add_missing_settings(project, app_id, team_id, enterprise=enterprise):
        ui.message("✓ App updated. Slack asks you to reinstall it when Tag connects.")


def check_existing_app(home: Path, config_path: Path, team_id: str, app_id: str, *, enterprise: bool) -> bool:
    """Show the app's checks. True to connect it, False to pick another app."""
    project = slack_project(home)
    while True:
        readable, output = remote_app_settings(project, app_id)
        missing = missing_app_settings(output, enterprise=enterprise) if readable else None
        if missing is None:
            checks = [{"label": "Read app settings", "ok": False,
                       "detail": "Slack CLI couldn't read this app's settings with your sign-in."}]
            entries = [("manual", "I'll do it myself"), ("check", "Check again"), ("back", "Back")]
        elif missing:
            checks = app_check_rows(missing, enterprise=enterprise)
            entries = [("update", "Update app"), ("manual", "I'll do it myself"),
                       ("check", "Check again"), ("back", "Back")]
        else:
            checks = app_check_rows([], enterprise=enterprise)
            entries = [("connect", "Connect"), ("back", "Back")]
        if not ui.protocol_active():
            print()
            for row in checks:
                ui.message(("✓ " if row["ok"] else "✗ ") + str(row["label"])
                           + (f" · {row['detail']}" if row["detail"] else ""))
            if missing:
                ui.message("Update app adds only Tag's missing settings and keeps the rest. Slack asks to reinstall.")
        ids = [option_id for option_id, _ in entries]
        prompt = "Ready to connect this app" if missing == [] else "This app needs a few changes"
        action = ids[ui.choose(prompt, [label for _, label in entries], qid="app_checks",
                               option_ids=ids, checks=checks, app_id=app_id)]
        if action == "connect":
            return True
        if action == "back":
            return False
        if action == "update":
            try:
                update_app_settings(home, team_id, app_id, enterprise)
            except RuntimeError as error:
                ui.message(str(error))
        elif action == "manual":
            ui.message(f"Open https://api.slack.com/apps/{app_id} and make these changes:")
            print_manual_steps(missing or [])
            if missing is None and confirm("Have you manually compared the app with Tag's manifest?",
                                           default=False, qid="manifest_compared"):
                return True


def choose_existing_app(home: Path, config_path: Path) -> str:
    """Your app: pick an app Tag knows, or paste a link or ID; then check it."""
    values = settings.load_config(config_path)
    team_id, workspace_id = slack_identity.cli_team(values), values.get("SLACK_TEAM_ID", "")
    enterprise = bool(values.get("SLACK_ENTERPRISE_ID"))
    project = slack_project(home)
    while True:
        apps = known_apps(home, team_id, workspace_id)
        free = [app for app in apps if not app["used_by"]]
        if not ui.protocol_active():
            for app in apps:
                if app["used_by"]:
                    ui.message(f"{app['name']} · already used by your Tag “{app['used_by']}”", code=ui.display.MUTED)
        ids = [str(app["id"]) for app in free] + ["other", "exit"]
        index = ui.choose("Which app?", [str(app["name"]) for app in free] + ["Use a different app", "Save and exit"],
                          qid="existing_app", option_ids=ids, apps=apps)
        if ids[index] == "exit":
            raise ui.Paused()
        app_id = ids[index] if index < len(free) else ask_app_id(workspace_id or team_id)
        ui.message(f"Selected app: {app_id}")
        link_app(home, config_path, team_id, app_id)
        if check_existing_app(home, config_path, team_id, app_id, enterprise=enterprise):
            settings.update_config(config_path, {"SLACK_APP_ID": app_id})
            ui.commit()  # The app is linked and chosen: Back stops here.
            return app_id
        if setup_progress(config_path).get("linked_by_setup") == app_id:
            unlink_local(project, team_id, app_id)
            save_progress(config_path, linked_by_setup=None)


def setup_progress(config_path: Path) -> dict:
    """Setup's own progress record: which path was chosen and what Slack showed."""
    return _read_json(config_path.with_name("setup-progress.json"))


def save_progress(config_path: Path, **changes) -> None:
    path = config_path.with_name("setup-progress.json")
    state = {**_read_json(path), **changes}
    settings.save_config(path, {key: value for key, value in state.items() if value is not None})


def validate_slack_identity(token: str, *, team_id: str = "", app_id: str = "", enterprise_id: str = "", label: str) -> dict[str, object]:
    payload = slack_permissions.recover(
        lambda: slack_identity.validate(token, team_id=team_id, app_id=app_id,
                                        enterprise_id=enterprise_id, label=label,
                                        api=slack_channels.slack_api), app_id)
    ui.message(f"✓ {label} authenticates for the selected workspace")
    return payload


def validate_socket_token(token: str, app_id: str = "") -> None:
    slack_permissions.recover(lambda: slack_channels.slack_api_post(token, "apps.connections.open"), app_id)
    ui.message("✓ Socket Mode app token opens a connection URL")


def connect_app_credentials(home: Path, config_path: Path, team_id: str, app_id: str) -> dict[str, str]:
    """Automatic connection is the default; manual token entry is an explicit fallback."""
    values = settings.load_config(config_path)
    if all(not settings.validation_error(key, values.get(key, ""))
           for key in ("SLACK_APP_TOKEN", "SLACK_BOT_TOKEN")):
        return values
    ui.notice("Connecting your app with Slack CLI…",
              "Renewing access; Slack may ask for approval. Credentials stay private.",
              footer="No app settings changed. No services or indexing started.")
    while True:
        try:
            credentials = slack_credentials.receive(slack_project(home), team_id, app_id,
                                                    **({"enterprise_id": values["SLACK_ENTERPRISE_ID"]}
                                                       if values.get("SLACK_ENTERPRISE_ID") else {}))
            validate_slack_identity(credentials["SLACK_BOT_TOKEN"], team_id=team_id,
                                    app_id=app_id, enterprise_id=values.get("SLACK_ENTERPRISE_ID", ""), label="Bot token")
            validate_socket_token(credentials["SLACK_APP_TOKEN"], app_id)
        except RuntimeError as error:
            if isinstance(error, slack_credentials.ConnectionFailure):
                ui.notice(error.title, error.detail, code=error.code, footer="Progress saved. Setup is paused.")
            else:
                ui.notice("Couldn't connect to Slack", str(error), footer="Progress saved. Setup is paused.")
            if isinstance(error, slack_permissions.MissingScope):
                slack_permissions.guidance(error, app_id)
            while True:
                action = ui.choose("Next step", [
                    "Retry connection", "Enter tokens manually", "Open app settings", "Save and exit",
                ], default=3, qid="credentials_next")
                if action == 3:
                    raise ui.Paused()
                if action == 1:
                    return values
                if action == 2:
                    slack_permissions.open_settings(app_id)
                    continue
                break
            continue
        # Commit the validated pair atomically. No unrelated settings are replaced.
        values = settings.update_config(config_path, credentials)
        ui.message("✓ Credentials connected and saved privately")
        ui.commit()
        return values


def connector_uri(team_id: str, app_id: str = "") -> str:
    suffix = f"-{app_id.lower()}" if app_id else ""
    return f"slack://tag-{team_id.lower()}{suffix}"


def connector_scope(team_id: str, channel: slack_channels.SlackChannel, app_id: str = "") -> str:
    safe_name = re.sub(r"[^\w.-]+", "-", channel.name).strip("-") or "unnamed"
    return f"{connector_uri(team_id, app_id)}/channels/{safe_name}__{channel.channel_id}"


def render_slack_connector(team_id: str, channels: list[slack_channels.SlackChannel], days: str,
                           *, credential: Path | None = None, app_id: str = "") -> str:
    ids = ", ".join(json.dumps(channel.channel_id) for channel in channels)
    types = sorted({"private_channel" if channel.is_private else "public_channel" for channel in channels})
    channel_types = ", ".join(json.dumps(value) for value in types)
    return "\n".join((
        "# mfs-server connector config — slack",
        f"# URI: {connector_uri(team_id, app_id)}",
        "# Generated by Tag. Contains no token; the credential is read from a private reference.",
        f"token = {json.dumps('file:' + str(credential) if credential else 'env:MFS_SLACK_TOKEN')}",
        f"team_id = {json.dumps(team_id)}",
        f"channel_types = [{channel_types}]",
        f"channel_ids = [{ids}]",
        f'oldest = "now-{days}d"',
        "max_read_rows = 100000",
        "",
    ))


def write_slack_connector(team_id: str, channels: list[slack_channels.SlackChannel], days: str, *,
                          home: Path | None = None, app_id: str = "",
                          credential: Path | None = None) -> Path:
    if not re.fullmatch(r"T[A-Z0-9]+", team_id):
        raise ValueError("Invalid Slack workspace ID")
    selected_home = home or instance_home()
    if not app_id:
        try:
            app_id = settings.load_config(selected_home / "config/settings.json").get("SLACK_APP_ID", "")
        except (OSError, ValueError):
            pass
    connector_dir = selected_home / "integrations/mfs/connectors"
    connector_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    identity = f"{team_id.lower()}-{app_id.lower()}" if app_id else team_id.lower()
    path = connector_dir / f"tag-{identity}.toml"
    default_credential = tag_credentials.slack_history_path(selected_home)
    reference = credential or (default_credential if default_credential.is_file() else None)
    content = render_slack_connector(team_id, channels, days, credential=reference, app_id=app_id)
    if path.exists() and path.read_text(encoding="utf-8") != content:
        backup = path.with_suffix(".toml.bak")
        shutil.copy2(path, backup)
        ui.message(f"Preserved previous connector configuration: {backup}")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
    if os.name != "nt":
        path.chmod(0o600)
    return path


def check_prerequisites(backend: str) -> bool:
    uv = shutil.which("uv")
    ui.message(
        "✓ uv: optional fast Python dependency runner"
        if uv else "· uv: optional; this installation can use Python venv and pip"
    )
    backend_found = shutil.which(backend)
    ui.message(f"{'✓' if backend_found else '✗'} {backend}: selected CLI backend")
    ok = bool(backend_found)

    installed_server = Path(sys.executable).parent / ("mfs-server.exe" if os.name == "nt" else "mfs-server")
    if not installed_server.is_file() and not shutil.which("mfs-server"):
        ui.message("✗ mfs-server: MFS memory server")
        mfs_server_spec = runtime_requirement("mfs-server")
        if shutil.which("uv") and confirm(f"Install {mfs_server_spec} with uv now?", qid="install_mfs"):
            completed = subprocess.run(
                ["uv", "tool", "install", "--force", mfs_server_spec], check=False
            )
            ok = ok and completed.returncode == 0 and shutil.which("mfs-server") is not None
        else:
            ok = False
    else:
        ui.message("✓ mfs-server: MFS memory server")
    return ok


def absolute_directory(prompt: str, default: Path, *, qid: str | None = None) -> Path:
    while True:
        value = Path(ask(prompt, str(default), qid=qid)).expanduser()
        if value.is_dir():
            return value.resolve()
        ui.message("That directory does not exist. Create it first or choose an existing workspace.")


def render_env(values: dict[str, str]) -> str:
    lines = [
        "# Generated by scripts/opentag_setup.py. Keep this file private.",
        "# It is ignored by Git and is limited to this account (chmod 600).",
        "",
    ]
    for key, value in values.items():
        lines.append(f"export {key}={shlex.quote(value)}")
    lines.append("")
    return "\n".join(lines)


def write_config(path: Path, values: dict[str, str]) -> None:
    if path.suffix == ".json":
        settings.save_config(path, values)
        print()
        ui.message(f"Wrote private configuration: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    content = json.dumps(values, indent=2) + "\n" if path.suffix == ".json" else render_env(values)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
    if os.name != "nt":
        path.chmod(0o600)
    print()
    ui.message(f"Wrote private configuration: {path}")


def creation_progress(config_path: Path):
    """Report app creation as progress lines: create, picture, install, connect."""
    progress = setup_progress(config_path)
    workspace = progress.get("workspace_name") or settings.load_config(config_path).get("SLACK_TEAM_ID", "")
    texts = {
        "create": "Create the Slack app",
        "picture": "Add the picture",
        "install": f"Add it to {workspace}" if progress.get("organization_name") else f"Install in {workspace}",
        "connect": "Connect to this Mac" if sys.platform == "darwin" else "Connect to this computer",
    }

    def report(step: str) -> None:
        if ui.protocol_active():
            ui.emit({"type": "progress", "step": step, "text": texts[step]})
        else:
            ui.message(f"◌ {texts[step]}")

    return report


def setup_recap(config_path: Path, project: Path) -> dict:
    """What Create in Slack will make, for the recap."""
    values = settings.load_config(config_path)
    progress = setup_progress(config_path)
    team_id = values.get("SLACK_TEAM_ID", "")
    enterprise_id = values.get("SLACK_ENTERPRISE_ID", "")
    picture = profile_picture(project)
    choice = tag_ai.default_choice(instance_home(), values)
    pictures = slack_setup_icons.pictures(tag_home(), instance_home(), values)
    return {
        "name": values.get("OPENTAG_BOT_NAME", ""),
        "description": values.get("OPENTAG_BOT_DESCRIPTION", ""),
        "picture": picture["preview"] if picture else None,
        "picture_revision": picture_revision(picture),
        "workspace": {
            "id": team_id, "name": progress.get("workspace_name") or team_id,
            "icon": pictures["workspace"],
            "organization": {"id": enterprise_id, "name": progress.get("organization_name") or enterprise_id}
            if enterprise_id else None,
        },
        "owner": {"id": values.get("SLACK_ALLOWED_USER_IDS", "").split(",")[0] or None,
                  "icon": pictures["owner"],
                  "name": progress.get("owner_name") or None},
        "ai": {key: choice[key] for key in ("value", "backend", "backend_name", "label")},
        "approval": bool(enterprise_id),
    }


def approve_creation(config_path: Path, project: Path, *, test_mode: bool = False) -> str:
    """The Create recap: one approval for the new app. Returns ``create`` or ``back``."""
    while True:
        recap = setup_recap(config_path, project)
        workspace = recap["workspace"]
        place = workspace["name"]
        if not ui.protocol_active():
            print()
            ui.display.section("Your Tag")
            ui.display.info_row("Name", recap["name"])
            if recap["description"]:
                ui.display.info_row("Description", recap["description"])
            picture = profile_picture(project)
            ui.display.info_row("Picture", picture["label"] if picture else "None")
            organization = workspace["organization"]
            ui.display.info_row("Workspace", f"{place} in {organization['name']}" if organization else place)
            owner = recap["owner"]
            ui.display.info_row("Owner", "You" + (f" · @{owner['name']}" if owner["name"] else f" · {owner['id']}"))
            ui.display.info_row("AI", f"{recap['ai']['backend_name']} · {recap['ai']['label']}")
            if recap["approval"]:
                ui.display.info_row("Approval", "An org admin may need to approve. Setup waits and resumes.")
            ui.message("Only you can ask it to work; people in the channel see its replies.", code=ui.display.MUTED)
            if test_mode:
                ui.message("TEST MODE · Continuing creates a real Slack app.", code=ui.display.WARNING)
        ids = ["create", "edit", "edit_ai", "back"]
        action = ids[ui.choose(f"Ready to create it in {place}?", ["Create in Slack", "Edit", "Edit AI", "Back"],
                               qid="approve_setup", option_ids=ids, recap=recap)]
        if action == "edit":
            choose_profile(project, config_path, editing=True, test_mode=test_mode)
        elif action == "edit_ai":
            tag_ai.setup_step(instance_home(), config_path)
        else:
            return action


def choose_workspace(config_path: Path) -> bool:
    """The Workspace step: save the workspace and make the signed-in account the owner."""
    selection = connect_slack_workspace()
    if not selection:
        return False
    owner = selection.user_id or signed_in_member(selection.sign_in_id or selection.enterprise_id
                                                  or selection.team_id, [])
    values = settings.update_config(config_path, {"SLACK_TEAM_ID": selection.team_id,
                                                  "SLACK_ENTERPRISE_ID": selection.enterprise_id})
    if settings.validation_error("SLACK_ALLOWED_USER_IDS", values.get("SLACK_ALLOWED_USER_IDS", "")):
        settings.update_config(config_path, {"SLACK_ALLOWED_USER_IDS": owner})
        ui.message(f"Owner: your Slack account ({owner}). Only you can ask this Tag to work.")
    save_progress(config_path, workspace_name=selection.name, organization_name=selection.organization_name or None,
                  owner_name=selection.user_name or None)
    if (instance_home() / "instance.json").is_file() and selection.name != selection.team_id:
        tag_instances.record_workspace_name(instance_home(), selection.name)
    return True


def guided_setup(
    config_path: Path, *, start_services: bool = True,
    review_channels: bool = False, test_mode: bool = False,
    telemetry_session: tag_telemetry.SetupSession | None = None,
) -> int:
    """Your Tag → AI → Workspace → Create (or Your app) → Channels → memory.

    Each step saves its answers, so a paused setup resumes at the first step
    still missing. Nothing touches Slack before the Workspace step.
    """
    values = settings.load_config(config_path)
    channel_policy = values.get("SLACK_CHANNEL_POLICY", "selected" if values.get("MFS_SLACK_CONNECTOR_CONFIG") else "invited")
    home = instance_home()
    initialize_instance(home)
    default_workspace = workspace_home(
        home, tag_home(), os.getenv("TAG_ID", "default")
    )
    workspace = Path(
        os.getenv("OPENTAG_WORKDIR", str(default_workspace))
    ).expanduser()
    if not workspace.is_absolute():
        raise ValueError("OPENTAG_WORKDIR must be an absolute path")
    initialize_workspace(workspace)
    if telemetry_session:
        telemetry_session.enter("slack")

    def show(step: int, title: str, detail: str = "") -> None:
        current = settings.load_config(config_path)
        ui.screen(step, title, detail, target=ui.display.target_detail(
            os.getenv("TAG_ID", "default"), current.get("SLACK_TEAM_ID", ""),
            current.get("SLACK_APP_ID", ""), current.get("OPENTAG_BOT_NAME", ""),
        ))

    # Defaults are not repeatedly prompted and never replace saved choices.
    defaults = {key: value for key, value in settings.DEFAULTS.items() if key not in values}
    if defaults:
        values = settings.update_config(config_path, defaults, only_missing=True)
    needs_slack_connection = any(
        settings.validation_error(key, values.get(key, ""))
        for key in ("SLACK_APP_TOKEN", "SLACK_BOT_TOKEN")
    )
    if needs_slack_connection:
        project = slack_project(home)
        profile_done = False
        while True:
            values = settings.load_config(config_path)
            app_path = setup_progress(config_path).get("app_path")
            has_app = bool(values.get("SLACK_APP_ID")) or (project / "tag-create.json").exists()
            # Until Slack is connected, a resumed setup starts at Your Tag with
            # its saved answers. Setups from before this order, paused after
            # choosing a workspace, start there too when the Tag isn't named yet.
            if not has_app and (ui.going_back_to("profile") or (
                    not profile_done and (not values.get("SLACK_TEAM_ID") or not app_path))):
                show(1, "Meet your new Tag", "Your progress is saved. Ctrl-C pauses setup.")
                app_path = choose_profile(project, config_path, test_mode=test_mode)
                save_progress(config_path, app_path=app_path)
                profile_done = True
            if telemetry_session:
                telemetry_session.enter("app")
            if not values.get("SLACK_TEAM_ID"):
                show(2, "Choose the AI")
            values = ensure_agent(config_path, settings.load_config(config_path), review=review_channels)
            if not values.get("SLACK_TEAM_ID"):
                show(3, "Which workspace?", "Tag uses your Slack sign-ins on this computer.")
                if not choose_workspace(config_path):
                    ui.message("Slack authorization is required; run tag setup again when ready.")
                    return 1
                values = settings.load_config(config_path)
            values = ensure_owner(config_path, values)
            if has_app:
                break
            if app_path == "existing":
                show(4, "Your app")
                choose_existing_app(home, config_path)
                break
            show(4, "Create your Tag", "Nothing is created in Slack until you approve it.")
            if approve_creation(config_path, project, test_mode=test_mode) == "create":
                break
            # Back from the recap: choose the workspace again.
            settings.save_config(config_path, {key: value for key, value in settings.load_config(config_path).items()
                                               if key not in OWNER_AND_WORKSPACE})
        values = settings.load_config(config_path)
        team_id = values["SLACK_TEAM_ID"]
        report = creation_progress(config_path)
        if setup_progress(config_path).get("app_path") == "existing" and values.get("SLACK_APP_ID"):
            app_id = values["SLACK_APP_ID"]
            link_app(home, config_path, slack_identity.cli_team(values), app_id)
        else:
            if not values.get("SLACK_APP_ID") and not slack_cli_supports_icon_upload():
                ui.message("Creating the app with its picture needs Slack CLI 4.7 or newer.")
                ui.message("Update Slack CLI, then run tag setup again. Your choices are saved.")
                raise ui.Paused()
            app_id = choose_slack_app(home, team_id, config_path, test_mode=test_mode, progress=report)
        values = settings.update_config(config_path, {"SLACK_APP_ID": app_id})
        if any(settings.validation_error(key, values.get(key, "")) for key in ("SLACK_APP_TOKEN", "SLACK_BOT_TOKEN")):
            report("connect")
        values = connect_app_credentials(home, config_path, team_id, app_id)
        if any(settings.validation_error(key, values.get(key, "")) for key in ("SLACK_APP_TOKEN", "SLACK_BOT_TOKEN")):
            print()
            ui.message("Connect your app · private credentials")
            ui.message(f"Settings: https://api.slack.com/apps/{app_id}")
            ui.message("Socket Mode: Basic Information → App-Level Tokens → connections:write.")
            ui.message("Bot: OAuth & Permissions → Bot User OAuth Token.")
        while settings.validation_error("SLACK_APP_TOKEN", values.get("SLACK_APP_TOKEN", "")):
            app_token = read_secret("Socket Mode app token (xapp-…)", qid="app_token")
            if error := settings.validation_error("SLACK_APP_TOKEN", app_token):
                ui.message(error)
                continue
            try:
                validate_socket_token(app_token, app_id)
            except slack_channels.SlackChannelError as exc:
                ui.message(str(exc))
                continue
            values = settings.update_config(config_path, {"SLACK_APP_TOKEN": app_token})
            break
        while settings.validation_error("SLACK_BOT_TOKEN", values.get("SLACK_BOT_TOKEN", "")):
            bot_token = read_secret("Bot token (xoxb-…)", qid="bot_token")
            if error := settings.validation_error("SLACK_BOT_TOKEN", bot_token):
                ui.message(error)
                continue
            try:
                validate_slack_identity(bot_token, team_id=team_id, app_id=app_id, enterprise_id=values.get("SLACK_ENTERPRISE_ID", ""), label="Bot token")
            except (slack_channels.SlackChannelError, RuntimeError) as exc:
                ui.message(str(exc))
                continue
            values = settings.update_config(config_path, {"SLACK_BOT_TOKEN": bot_token})
            break
    else:
        # Already connected: review the AI step only when asked to.
        values = ensure_agent(config_path, values, review=review_channels)

    if telemetry_session:
        telemetry_session.enter("app")
    bot_identity = validate_slack_identity(
        values["SLACK_BOT_TOKEN"],
        team_id=values.get("SLACK_TEAM_ID", ""),
        app_id=values.get("SLACK_APP_ID", ""),
        enterprise_id=values.get("SLACK_ENTERPRISE_ID", ""),
        label="Bot token",
    )
    validate_socket_token(values["SLACK_APP_TOKEN"], values.get("SLACK_APP_ID", ""))
    inferred: dict[str, str] = {}
    if not values.get("SLACK_TEAM_ID") and isinstance(bot_identity.get("team_id"), str):
        inferred["SLACK_TEAM_ID"] = str(bot_identity["team_id"])
    if not values.get("SLACK_APP_ID") and isinstance(bot_identity.get("app_id"), str):
        inferred["SLACK_APP_ID"] = str(bot_identity["app_id"])
    if inferred:
        values = settings.update_config(config_path, inferred)
    # Setups from before the owner came from the sign-in may not have one yet.
    values = ensure_owner(config_path, values)

    if telemetry_session:
        telemetry_session.enter("channels")
    tag_name = values.get("OPENTAG_BOT_NAME") or "Tag"
    show(5, f"Where should {tag_name} start?", "Slack connected")
    if not values.get("SLACK_CHANNEL_IDS") and values.get("SLACK_CHANNEL_ID"):
        values = settings.update_config(
            config_path, {"SLACK_CHANNEL_IDS": values["SLACK_CHANNEL_ID"]}
        )
    follows_invitations = channel_policy == "invited"
    selected_channels: list[slack_channels.SlackChannel] = []

    def choose_setup_channels():
        return slack_permissions.recover(
            lambda: slack_channels.setup_channels(
                values["SLACK_BOT_TOKEN"], values.get("SLACK_CHANNEL_IDS", ""), tag_name=tag_name,
                allow_empty=follows_invitations, app_id=values.get("SLACK_APP_ID", ""),
                **({"team_id": values["SLACK_TEAM_ID"]} if values.get("SLACK_ENTERPRISE_ID") else {})),
            values.get("SLACK_APP_ID", ""),
        )

    def save_channels(channels: list[slack_channels.SlackChannel]) -> dict[str, str]:
        changes = {"SLACK_CHANNEL_IDS": ",".join(channel.channel_id for channel in channels)}
        if follows_invitations:
            # New setups follow invitations: channels Tag is invited to later are picked up.
            changes["SLACK_CHANNEL_POLICY"] = "invited"
        saved = settings.update_config(config_path, changes)
        save_progress(config_path, channels_done=True)
        return saved

    chosen_before = ((follows_invitations and setup_progress(config_path).get("channels_done") is True)
                     or not settings.validation_error("SLACK_CHANNEL_IDS", values.get("SLACK_CHANNEL_IDS", "")))
    if review_channels or not chosen_before:
        selected_channels = choose_setup_channels()
        values = save_channels(selected_channels)
    elif values.get("SLACK_CHANNEL_IDS"):
        available = slack_permissions.recover(
            lambda: slack_channels.list_channels(values["SLACK_BOT_TOKEN"],
                **({"team_id": values["SLACK_TEAM_ID"]} if values.get("SLACK_ENTERPRISE_ID") else {})), values.get("SLACK_APP_ID", ""),
        )
        visible = {channel.channel_id: channel for channel in available}
        selected_channels = [
            visible[channel_id]
            for channel_id in slack_channels.parse_channel_ids(values["SLACK_CHANNEL_IDS"])
            if channel_id in visible and visible[channel_id].is_member
        ]
        if {channel.channel_id for channel in selected_channels} != set(
                slack_channels.parse_channel_ids(values["SLACK_CHANNEL_IDS"])):
            ui.message("A saved channel is no longer available. Choose channels again.")
            selected_channels = choose_setup_channels()
            values = save_channels(selected_channels)
    elif follows_invitations and values.get("SLACK_CHANNEL_POLICY") != "invited":
        values = settings.update_config(config_path, {"SLACK_CHANNEL_POLICY": "invited"})
    if selected_channels:
        ui.message("Channels: " + ", ".join(f"#{c.name}" for c in selected_channels))
    elif follows_invitations:
        ui.message(f"No channels yet. Invite {tag_name} with /invite in Slack; it starts there within a minute.")

    if telemetry_session:
        telemetry_session.enter("finish")
    ui.message("✓ Slack connected\n◌ Preparing Slack memory…")
    uri = connector_uri(values["SLACK_TEAM_ID"], values.get("SLACK_APP_ID", ""))
    required_scopes = [connector_scope(values["SLACK_TEAM_ID"], channel,
                                       values.get("SLACK_APP_ID", "")) for channel in selected_channels]
    legacy_scopes = [connector_scope(values["SLACK_TEAM_ID"], channel)
                     for channel in selected_channels]
    saved_scopes = [scope.strip() for scope in values.get("MFS_ALLOWED_SCOPES", "").split(",") if scope.strip()]
    saved_connector = Path(values.get("MFS_SLACK_CONNECTOR_CONFIG", ""))
    legacy_uri = connector_uri(values["SLACK_TEAM_ID"])
    expected_connectors = {
        render_slack_connector(
            values["SLACK_TEAM_ID"], selected_channels, values["MFS_SLACK_HISTORY_DAYS"],
            credential=tag_credentials.slack_history_path(home),
            app_id=values.get("SLACK_APP_ID", ""),
        ),
        render_slack_connector(values["SLACK_TEAM_ID"], selected_channels,
                               values["MFS_SLACK_HISTORY_DAYS"]),
    }
    if selected_channels:
        memory_incomplete = (
            not values.get("MFS_SLACK_TOKEN")
            or values.get("MFS_SLACK_CONNECTOR_URI") not in {uri, legacy_uri}
            or not saved_connector.is_file()
            or not (set(required_scopes).issubset(saved_scopes)
                    or set(legacy_scopes).issubset(saved_scopes))
            or saved_connector.read_text(encoding="utf-8") not in expected_connectors
        )
    else:
        # No channels yet: keep the history credential ready. Following
        # invitations writes the connector once Tag is in a channel, and an
        # empty channel list is never sent to memory.
        memory_incomplete = (not values.get("MFS_SLACK_TOKEN")
                             or values.get("MFS_SLACK_CONNECTOR_URI") not in {uri, legacy_uri})
    if memory_incomplete:
        history_token = values.get("MFS_SLACK_TOKEN") or values["SLACK_BOT_TOKEN"]
        while True:
            try:
                validate_slack_identity(history_token, team_id=values.get("SLACK_TEAM_ID", ""), enterprise_id=values.get("SLACK_ENTERPRISE_ID", ""), label="Slack-history credential")
                for channel in selected_channels:
                    slack_channels.slack_api(history_token, "conversations.history", {"channel": channel.channel_id, "limit": "1"})
                break
            except (slack_channels.SlackChannelError, RuntimeError) as exc:
                ui.message(f"History access needs attention: {exc}")
                if isinstance(exc, slack_permissions.MissingScope):
                    slack_permissions.guidance(exc, values.get("SLACK_APP_ID", ""))
                action = ui.choose("Continue with saved channels", ["Check again", "Use a different history credential", "Save and exit"], qid="saved_channels")
                if action == 2:
                    raise ui.Paused()
                if action == 1:
                    history_token = ask_secret("Slack-history token (hidden)", "xox", qid="history_token")
        if not lifecycle.local_mfs_endpoint(
            values.get("MFS_URL", settings.DEFAULTS["MFS_URL"])
        ):
            raise RuntimeError(
                "Slack history for a remote MFS endpoint needs a server-resolvable credential reference; "
                "a local Tag credential file cannot be used remotely."
            )
        tag_credentials.write_slack_history(home, history_token)
        changes = {"MFS_SLACK_TOKEN": history_token, "MFS_SLACK_CONNECTOR_URI": uri}
        if selected_channels:
            connector = write_slack_connector(
                values["SLACK_TEAM_ID"], selected_channels, values["MFS_SLACK_HISTORY_DAYS"], home=home
            )
            changes.update(MFS_ALLOWED_SCOPES=",".join(dict.fromkeys([*saved_scopes, *required_scopes])),
                           MFS_SLACK_CONNECTOR_CONFIG=str(connector))
        values = settings.update_config(config_path, changes)
        if selected_channels:
            ui.message(f"Configured selected-channel Slack memory: {changes['MFS_SLACK_CONNECTOR_CONFIG']}")
    errors = settings.config_errors(values)
    if errors:
        print()
        ui.message("Some saved settings need attention. Update them in Settings or with tag config set:")
        for key, error in errors.items():
            ui.message(f"{key}: {error}")
        return 1
    if not start_services:
        print()
        ui.message("No services were started; no history was indexed.")
        ui.message("Do not run tag start for this test until its separate MFS server is configured.")
        return 0
    return finish_setup(config_path, values, selected_channels)


def finish_setup(config_path: Path, values: dict[str, str], _channels: list[slack_channels.SlackChannel]) -> int:
    """Finish configuration without starting services or indexing history."""
    ui.message("✓ Slack memory configured")
    backend, _ = agent_models.parse_model_choice(
        values.get("OPENTAG_DEFAULT_MODEL") or values["OPENTAG_BACKEND"], values["OPENTAG_BACKEND"])
    try:
        from . import agent_connection
    except ImportError:
        import agent_connection
    try:
        agent_connection.validate(backend, values)
    except ValueError as exc:
        ui.message(str(exc))
        return 1
    if agent_connection.active(backend, values):
        ui.message(f"✓ {backend.title()} API connection configured · first task still unverified")
    else:
        # Sign-ins can lapse while setup waits; check the default agent once more.
        while True:
            backend, _ = agent_models.parse_model_choice(
                values.get("OPENTAG_DEFAULT_MODEL") or values["OPENTAG_BACKEND"], values["OPENTAG_BACKEND"])
            agent = tag_ai.connection(instance_home(), backend)
            if agent["state"] == "connected":
                break
            ui.message(f"{agent['name']} isn't ready: {tag_ai.status_line(agent)}. Your Slack and memory choices are saved.")
            values = tag_ai.setup_step(instance_home(), config_path)
        ui.message(f"✓ {agent['name']} connected · {agent['account'] or 'signed in'}")
    print()
    ui.message("✓ Setup complete. No services were started and no history was indexed.")
    if len(set(values.get("SLACK_ALLOWED_USER_IDS", "").split(","))) == 1 and values.get("SLACK_ALLOWED_USER_IDS"):
        ui.message("Once Tag starts successfully, it will send your configured Slack account a welcome DM with the community help link.")
    tag_id = os.getenv("TAG_ID", "default")
    command = "tag start" if tag_id == "default" else f"tag {shlex.quote(tag_id)} start"
    ui.display.next_action(
        "Next step · start Tag",
        command,
        detail="Tag is still stopped. Run this command to connect Slack and make Tag available.",
    )
    return 0


def completed_setup_status(config_path: Path) -> int | None:
    """Completed onboarding is a health check, not another indexing approval."""
    progress = config_path.with_name("setup-progress.json")
    if progress.exists():
        state = slack_app_create.read_object(progress)
        if state.get("completed") is not True:
            return None
    values = settings.load_config(config_path)
    # A Tag that follows invitations may have no channels, and no connector, yet.
    no_channels_yet = values.get("SLACK_CHANNEL_POLICY") == "invited" and not values.get("SLACK_CHANNEL_IDS")
    if (settings.config_errors(values) or not values.get("MFS_SLACK_TOKEN")
            or not (no_channels_yet or (values.get("SLACK_CHANNEL_IDS")
                                        and Path(values.get("MFS_SLACK_CONNECTOR_CONFIG", "")).is_file()))):
        return None
    try:
        from . import tag_control, tag_cli
    except ImportError:
        import tag_control, tag_cli
    report = tag_control.status_report(instance_home(), tag_cli, tag_id=os.getenv("TAG_ID", "default"))
    ui.message("Your setup is already saved. Checking readiness; no settings were changed.")
    tag_control.show_status(report)
    ui.message("Change choices with tag settings, or review with tag setup --review.")
    return int(report["state"] not in {"running", "stopped"})


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up Tag or resume missing configuration.")
    parser.add_argument("--config", type=Path, default=instance_home() / "config/settings.json", help="configuration file to create")
    parser.add_argument("--no-start", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--review", action="store_true", help="review completed setup choices")
    parser.add_argument("--test-mode", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--review-channels", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--completion-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    config_path = args.config.expanduser().resolve()
    ui.enter_protocol()
    if not ui.display.stdin_is_terminal() and not ui.protocol_active():
        print("Use a terminal for setup, or tag inspect --json and tag config set for automation.", file=sys.stderr)
        return 2
    telemetry_session: tag_telemetry.SetupSession | None = None
    try:
        os.environ["OPENTAG_ENV_FILE"] = str(config_path)
        if not args.review:
            completed = completed_setup_status(config_path)
            if completed is not None:
                return completed
        progress = config_path.with_name("setup-progress.json")
        # Keep the steps an earlier, paused setup recorded (such as which app path).
        settings.save_config(progress, {**_read_json(progress), "completed": False})
        setup_options = {
            # This selects the full readiness path; finish_setup deliberately
            # leaves service startup to the separate `tag start` command.
            "start_services": not args.no_start,
            "review_channels": args.review_channels,
        }
        if args.test_mode:
            setup_options["test_mode"] = True
        telemetry_session = tag_telemetry.SetupSession(
            tag_home(), "test" if args.test_mode else "setup"
        )
        telemetry_session.start()
        setup_options["telemetry_session"] = telemetry_session
        while True:
            try:
                result = guided_setup(config_path, **setup_options)
                break
            except ui.GoBack as back:
                # Forget only what the earlier question saved, then replay to it.
                cleared = BACK_CLEARS.get(back.target[0], ())
                if cleared:
                    values = settings.load_config(config_path)
                    settings.save_config(config_path, {k: v for k, v in values.items() if k not in cleared})
                if back.target[0] == "channels":
                    save_progress(config_path, channels_done=None)
                ui.start_replay(back.replay, back.target)
        if result == 0:
            backend = settings.load_config(config_path).get("OPENTAG_BACKEND", "")
            telemetry_session.complete(backend)
        else:
            telemetry_session.abandon()
        if result == 0:
            settings.save_config(progress, {**_read_json(progress), "completed": True, "services_requested": False})
        if result == 0 and args.completion_file:
            settings.save_config(args.completion_file, {"approved": True})
        return result
    except ui.Paused:
        if telemetry_session:
            telemetry_session.abandon()
        print()
        ui.message("Setup is incomplete. Progress saved; run tag setup to continue.")
        return 0
    except (KeyboardInterrupt, EOFError):
        if telemetry_session:
            telemetry_session.abandon()
        print()
        ui.message("Setup paused. Saved answers are kept; run tag setup to continue.")
        return 130
    except (OSError, ValueError, RuntimeError) as exc:
        if telemetry_session:
            telemetry_session.abandon()
        print(f"Setup could not continue: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
