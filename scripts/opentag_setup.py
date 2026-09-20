#!/usr/bin/env python3
"""Create a private Tag configuration without storing secrets in Git."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

try:
    from tag_paths import tag_home, initialize
    import tag_config as settings
    import slack_channels
    import setup_ui as ui
    import slack_permissions
    import slack_app_create
    import slack_credentials
except ImportError:
    from scripts.tag_paths import tag_home, initialize
    from scripts import slack_channels, tag_config as settings
    from scripts import setup_ui as ui
    from scripts import slack_permissions
    from scripts import slack_app_create
    from scripts import slack_credentials


@dataclass(frozen=True)
class BackendOption:
    key: str
    name: str
    note: str
    install_url: str


BACKEND_OPTIONS = (
    BackendOption(
        key="codex",
        name="Codex",
        note="Recommended and supported",
        install_url="https://learn.chatgpt.com/docs/codex/cli",
    ),
    BackendOption(
        key="claude",
        name="Claude Code",
        note="Experimental",
        install_url="https://code.claude.com/docs/en/setup",
    ),
)


REQUIRED_APP_SETTINGS = {
    "Socket Mode": "socket_mode_enabled: true",
    "app mentions": "app_mention",
    "App Home event (app_home_opened)": "app_home_opened",
    "agent stop event": "agent_session_stopped",
    "Home tab enabled": "home_tab_enabled: true",
    "Messages tab enabled": "messages_tab_enabled: true",
    "Agent view enabled": "agent_view",
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


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or (default or "")


def ask_required(prompt: str) -> str:
    while True:
        value = ask(prompt)
        if value:
            return value
        ui.message("A value is required.")


def choose_backend() -> str:
    ui.message("Choose the local agent that will run Tag tasks:")
    print()
    for index, option in enumerate(BACKEND_OPTIONS, start=1):
        availability = "installed" if shutil.which(option.key) else "not found"
        ui.message(f"{index}. {option.name:<12} {option.note:<27} {availability}")

    aliases = {
        "1": "codex",
        "codex": "codex",
        "2": "claude",
        "claude": "claude",
        "claude code": "claude",
    }
    while True:
        answer = ask("\nAgent", "1").lower()
        backend = aliases.get(answer)
        if backend:
            return backend
        ui.message("Choose 1 for Codex or 2 for Claude Code.")


def selected_backend_available(backend: str) -> bool:
    option = next(option for option in BACKEND_OPTIONS if option.key == backend)
    if shutil.which(option.key):
        return True

    print()
    ui.message(f"{option.name} was selected, but `{option.key}` is not available on PATH.")
    ui.message(f"Install and sign in first: {option.install_url}")
    ui.message("Then run ./tag setup again.")
    return False


def ask_secret(prompt: str, prefix: str) -> str:
    while True:
        value = getpass.getpass(f"{prompt}: ").strip()
        if value.startswith(prefix):
            return value
        ui.message(f"Enter the {prefix} token issued by Slack.")


def confirm(prompt: str, default: bool = True) -> bool:
    choice = "Y/n" if default else "y/N"
    answer = input(f"{prompt} [{choice}]: ").strip().lower()
    return default if not answer else answer in {"y", "yes"}


TOKEN_RE = re.compile(r"xox(?:a|b|p|s|r)?-[A-Za-z0-9-]+|xapp-[A-Za-z0-9-]+")


def safe_cli_output(value: str) -> str:
    return TOKEN_RE.sub("[redacted Slack token]", value)


def run_slack_cli(arguments: list[str], *, cwd: Path | None = None, interactive: bool = False, quiet: bool = False) -> int:
    command = [shutil.which("slack") or "slack", *arguments, "--skip-update"]
    if interactive:
        return subprocess.run(command, cwd=cwd, check=False).returncode
    completed = subprocess.run(command, cwd=cwd, check=False, text=True, capture_output=True)
    output = safe_cli_output("\n".join(part for part in (completed.stdout, completed.stderr) if part).strip())
    if output and (not quiet or completed.returncode):
        ui.message(output)
    return completed.returncode


def inspect_slack_app(project: Path, app_id: str, *, issues: list[str] | None = None) -> bool:
    """Inspect remote settings without changing them; fall back to guided review."""
    if issues is not None:
        issues.clear()
    command = [
        shutil.which("slack") or "slack", "manifest", "info", "--source", "remote",
        "--app", app_id, "--skip-update", "--no-color",
    ]
    completed = subprocess.run(command, cwd=project, check=False, text=True, capture_output=True)
    output = safe_cli_output(completed.stdout or "")
    normalized = output.replace('"', "").replace("'", "")
    required = REQUIRED_APP_SETTINGS
    if completed.returncode == 0 and output:
        missing = [
            label
            for label, marker in required.items()
            if not re.search(
                rf"(?<![A-Za-z0-9_:]){re.escape(marker)}(?![A-Za-z0-9_:])",
                normalized,
            )
        ]
        if not missing:
            ui.message("✓ Existing app has Tag's required Socket Mode, events, and bot scopes")
            return True
        ui.message("App configuration needs attention: " + ", ".join(missing))
        if issues is not None:
            issues.extend(missing)
        ui.message("Open app settings, make the listed changes, then choose Check again.")
        print()
        ui.message("In Slack app settings:")
        if any(label in missing for label in (
            "app mentions", "App Home event (app_home_opened)", "agent stop event", "direct-message event"
        )):
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
            ui.message("Agents & AI Apps → enable the Agent messaging experience.", indent="    ")
        if "Interactive controls enabled" in missing:
            ui.message("Interactivity & Shortcuts → enable Interactivity.", indent="    ")
        if "Socket Mode" in missing:
            ui.message("Socket Mode → enable Socket Mode.", indent="    ")
        scopes = [required[label] for label in missing if ":" in required[label] and " " not in required[label]]
        if scopes:
            ui.message("OAuth & Permissions → add bot scopes: " + ", ".join(scopes), indent="    ")
            ui.message("Reinstall the app after adding scopes; Slack may require administrator approval.", indent="    ")
        ui.message("Keep existing settings, then save your changes.")
        return False
    detail = safe_cli_output((completed.stderr or completed.stdout).strip())
    if detail:
        ui.message(detail)
    ui.message("Slack CLI could not inspect the remote manifest with this authorization.")
    return confirm("Have you manually compared the app with Tag's manifest?", default=False)


def authorized_workspaces(output: str) -> list[tuple[str, str]]:
    """Parse the CLI's text account listing without reading its credential files."""
    output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
    workspaces: dict[str, str] = {}
    for line in output.splitlines():
        match = re.fullmatch(r"\s*(.+?)\s+\(Team ID:\s*(T[A-Z0-9]+)\)\s*", line)
        if match:
            name, team_id = match.groups()
            workspaces.setdefault(team_id, name.strip())
    return [(name, team_id) for team_id, name in workspaces.items()]


def authorized_members(output: str, team_id: str) -> list[str]:
    """Read member IDs only from account blocks for the selected workspace."""
    output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
    selected = False
    members: list[str] = []
    for line in output.splitlines():
        if "Team ID:" in line:
            match = re.fullmatch(r"\s*.+?\s+\(Team ID:\s*(T[A-Z0-9]+)\)\s*", line)
            selected = bool(match and match.group(1) == team_id)
        elif selected:
            match = re.fullmatch(r"\s*User ID:\s*([UW][A-Z0-9]+)\s*", line)
            if match and match.group(1) not in members:
                members.append(match.group(1))
    return members


def choose_allowed_users(team_id: str, current: str = "") -> str:
    if not settings.validation_error("SLACK_ALLOWED_USER_IDS", current):
        return current
    members: list[str] = []
    slack = shutil.which("slack")
    if slack and re.fullmatch(r"T[A-Z0-9]+", team_id):
        try:
            result = subprocess.run(
                [slack, "auth", "list", "--skip-update", "--no-color"],
                check=False, text=True, capture_output=True, timeout=15,
            )
            if result.returncode == 0:
                members = authorized_members(result.stdout + "\n" + result.stderr, team_id)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if members:
        options = [f"Use my Slack account ({member})" for member in members]
        options += ["Choose someone else", "Save and exit"]
        choice = ui.choose("Who can use Tag?", options)
        if choice < len(members):
            return members[choice]
        if choice == len(members) + 1:
            raise ui.Paused()
    else:
        ui.message("Could not identify your Slack account for this workspace. Enter your member ID below.")
    return ask_validated(
        "Slack member ID (profile > More > Copy member ID)", "SLACK_ALLOWED_USER_IDS"
    )


def connect_slack_cli(current: str = "") -> str | None:
    if not shutil.which("slack"):
        ui.message("Slack CLI is required for workspace authorization: https://docs.slack.dev/tools/slack-cli/")
        return None
    while True:
        result = subprocess.run(
            [shutil.which("slack") or "slack", "auth", "list", "--skip-update", "--no-color"],
            check=False, text=True, capture_output=True,
        )
        accounts = authorized_workspaces(result.stdout + "\n" + result.stderr) if result.returncode == 0 else []
        if not accounts:
            ui.message("No authorized workspaces could be listed. Connect through Slack CLI to continue.")
        options = [name + (" (saved)" if team_id == current else "") for name, team_id in accounts]
        options += ["Connect another workspace" if accounts else "Connect Slack", "Save and exit"]
        index = ui.choose("Choose a workspace", options)
        if index < len(accounts):
            name, team_id = accounts[index]
            ui.message(f"✓ {name}")
            return team_id
        if index == len(accounts) + 1:
            return None
        if run_slack_cli(["auth", "login"], interactive=True):
            ui.message("Slack CLI authorization was not completed. Run tag setup to try again.")
            return None


def ask_validated(prompt: str, key: str, default: str | None = None) -> str:
    while True:
        value = ask(prompt, default)
        if not (error := settings.validation_error(key, value)):
            return value
        ui.message(error)


def slack_project(home: Path) -> Path:
    """Create Tag-owned Slack CLI metadata without touching the source checkout."""
    project = home / "integrations/slack-cli"
    (project / ".slack").mkdir(parents=True, exist_ok=True, mode=0o700)
    config = project / ".slack/config.json"
    if not config.exists():
        config.write_text(json.dumps({"manifest": {"source": "remote"}}, indent=2) + "\n")
    # Remote-manifest management needs project metadata but no SDK run hooks:
    # Tag supervises its own bridge rather than using `slack run`.
    hooks = project / ".slack/hooks.json"
    if not hooks.exists():
        hooks.write_text(json.dumps({"hooks": {}}, indent=2) + "\n")
    return project


def saved_slack_app(project: Path, team_id: str, app_id: str) -> bool:
    """Reuse Slack CLI's own link records, including links made before Tag checkpoints."""
    saved_ids = slack_app_create.saved_app_ids(project, team_id)
    if app_id in saved_ids:
        return True
    if saved_ids:
        raise RuntimeError("This workspace is already linked to another app (" + ", ".join(sorted(saved_ids)) +
                           "). Select that App ID in Tag settings or use a separate Tag home for the other app. Existing links were kept.")
    return False


def choose_slack_app(home: Path, team_id: str, config_path: Path | None = None) -> str:
    config_path = config_path or settings.config_path(home)
    values = settings.load_config(config_path)
    app_id = values.get("SLACK_APP_ID", "")
    ui.screen(2, "Which app should Tag use?")
    project = slack_project(home)
    creation = project / "tag-create.json"
    if creation.exists():
        state = slack_app_create.read_object(creation)
        # An explicitly supplied ID can recover an uncertain creation via normal linking.
        if not app_id or state.get("app_id") == app_id:
            app_id = slack_app_create.create_app(project, team_id, config_path, run_slack_cli)
    if not app_id:
        ui.message(f"Workspace: {team_id}")
        action = ui.choose("Slack app", [
            "Create a new Tag app", "Use an existing app", "Save and exit",
        ])
        if action == 2:
            raise ui.Paused()
        if action == 0:
            app_id = slack_app_create.create_app(project, team_id, config_path, run_slack_cli)
        else:
            print()
            ui.message("Find your App ID:")
            ui.message("1. Open https://api.slack.com/apps (sign in if asked).")
            ui.message(f"2. Select an app you manage for workspace {team_id}.")
            ui.message("3. Go to Basic Information → App Credentials → App ID.")
            ui.message("4. Copy the ID starting with A and paste it below.")
            ui.message("This is not a token or Client ID. Ctrl+C exits without saving an ID.")
            app_id = ask_validated("App ID", "SLACK_APP_ID")
            settings.update_config(config_path, {"SLACK_APP_ID": app_id})
    ui.message(f"Selected app: {app_id}")
    # Keep link progress separately from credential validation, including across exits.
    marker = project / "tag-linked.json"
    if not saved_slack_app(project, team_id, app_id):
        ui.message("Linking keeps your app's existing permissions.")
        if ui.choose("Continue with this app?", ["Link app and check settings", "Save and exit"]) == 1:
            raise ui.Paused()
        while not saved_slack_app(project, team_id, app_id):
            result = run_slack_cli(
                ["app", "link", "--team", team_id, "--app", app_id, "--environment", "local"], cwd=project, quiet=True,
            )
            if saved_slack_app(project, team_id, app_id):
                break
            if result == 0:
                ui.message("Slack returned success, but the app link could not be confirmed locally.")
            if ui.choose("App linking needs attention", ["Check again", "Save and exit"]) == 1:
                raise ui.Paused()
    settings.save_config(marker, {"app_id": app_id, "team_id": team_id})
    ui.message("✓ App linked")
    issues: list[str] = []
    while not inspect_slack_app(project, app_id, issues=issues):
        print()
        ui.message("Your app selection and link are saved.")
        while True:
            choice = ui.choose("App settings need attention", ["Open app settings", "Check again", "Save and exit"])
            if choice == 0:
                webbrowser.open(f"https://api.slack.com/apps/{app_id}")
            elif choice == 1:
                break
            else:
                raise ui.Paused()
    return app_id


def validate_slack_identity(token: str, *, team_id: str = "", app_id: str = "", label: str) -> dict[str, object]:
    payload = slack_permissions.recover(lambda: slack_channels.slack_api(token, "auth.test", {}), app_id)
    if team_id and payload.get("team_id") != team_id:
        raise RuntimeError(f"{label} belongs to a different Slack workspace")
    if app_id and payload.get("app_id") and payload.get("app_id") != app_id:
        raise RuntimeError(f"{label} belongs to a different Slack app")
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
            credentials = slack_credentials.receive(slack_project(home), team_id, app_id)
            validate_slack_identity(credentials["SLACK_BOT_TOKEN"], team_id=team_id,
                                    app_id=app_id, label="Bot token")
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
                ], default=3)
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
        return values


def connector_scope(team_id: str, channel: slack_channels.SlackChannel) -> str:
    safe_name = re.sub(r"[^\w.-]+", "-", channel.name).strip("-") or "unnamed"
    return f"slack://tag-{team_id.lower()}/channels/{safe_name}__{channel.channel_id}"


def render_slack_connector(team_id: str, channels: list[slack_channels.SlackChannel], days: str) -> str:
    ids = ", ".join(json.dumps(channel.channel_id) for channel in channels)
    types = sorted({"private_channel" if channel.is_private else "public_channel" for channel in channels})
    channel_types = ", ".join(json.dumps(value) for value in types)
    return "\n".join((
        "# mfs-server connector config — slack",
        f"# URI: slack://tag-{team_id.lower()}",
        "# Generated by Tag. Contains no token; the credential is read from the Tag environment.",
        'token = "env:MFS_SLACK_TOKEN"',
        f"channel_types = [{channel_types}]",
        f"channel_ids = [{ids}]",
        f'oldest = "now-{days}d"',
        "max_read_rows = 100000",
        "",
    ))


def write_slack_connector(team_id: str, channels: list[slack_channels.SlackChannel], days: str, *, home: Path | None = None) -> Path:
    if not re.fullmatch(r"T[A-Z0-9]+", team_id):
        raise ValueError("Invalid Slack workspace ID")
    connector_dir = (home or tag_home()) / "integrations/mfs/connectors"
    connector_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = connector_dir / f"tag-{team_id.lower()}.toml"
    content = render_slack_connector(team_id, channels, days)
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
    ok = True
    for command, purpose in (("uv", "Python dependency runner"), (backend, "selected CLI backend")):
        found = shutil.which(command)
        ui.message(f"{'✓' if found else '✗'} {command}: {purpose}")
        ok = ok and bool(found)

    installed_server = Path(sys.executable).parent / ("mfs-server.exe" if os.name == "nt" else "mfs-server")
    if not installed_server.is_file() and not shutil.which("mfs-server"):
        ui.message("✗ mfs-server: MFS memory server")
        mfs_server_spec = runtime_requirement("mfs-server")
        if shutil.which("uv") and confirm(f"Install {mfs_server_spec} with uv now?"):
            completed = subprocess.run(
                ["uv", "tool", "install", "--force", mfs_server_spec], check=False
            )
            ok = ok and completed.returncode == 0 and shutil.which("mfs-server") is not None
        else:
            ok = False
    else:
        ui.message("✓ mfs-server: MFS memory server")
    return ok


def absolute_directory(prompt: str, default: Path) -> Path:
    while True:
        value = Path(ask(prompt, str(default))).expanduser()
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


def guided_setup(config_path: Path, *, start_services: bool = True, review_channels: bool = False) -> int:
    values = settings.load_config(config_path)
    channel_policy = values.get("SLACK_CHANNEL_POLICY", "selected" if values.get("MFS_SLACK_CONNECTOR_CONFIG") else "invited")
    home = tag_home()
    initialize(home)
    ui.screen(1, "Let’s connect Tag to Slack.", "Your progress is saved. Ctrl-C pauses setup.")

    # Defaults are not repeatedly prompted and never replace saved choices.
    defaults = {key: value for key, value in settings.DEFAULTS.items() if key not in values}
    if defaults:
        values = settings.update_config(config_path, defaults, only_missing=True)
    if settings.validation_error("OPENTAG_BACKEND", values["OPENTAG_BACKEND"]):
        values = settings.update_config(config_path, {"OPENTAG_BACKEND": choose_backend()})
    backend = values["OPENTAG_BACKEND"]
    if not selected_backend_available(backend):
        return 1

    needs_slack_connection = any(
        settings.validation_error(key, values.get(key, ""))
        for key in ("SLACK_APP_TOKEN", "SLACK_BOT_TOKEN")
    )
    if needs_slack_connection:
        ui.message("Authorize access in Slack. Private credentials stay in this terminal.")
        team_id = values.get("SLACK_TEAM_ID", "") or connect_slack_cli()
        if not team_id:
            ui.message("Slack authorization is required; run tag setup again when ready.")
            return 1
        values = settings.update_config(config_path, {"SLACK_TEAM_ID": team_id})
        app_id = choose_slack_app(home, team_id, config_path)
        values = settings.update_config(config_path, {"SLACK_APP_ID": app_id})
        values = connect_app_credentials(home, config_path, team_id, app_id)
        if any(settings.validation_error(key, values.get(key, "")) for key in ("SLACK_APP_TOKEN", "SLACK_BOT_TOKEN")):
            print()
            ui.message("Connect your app · private credentials")
            ui.message(f"Settings: https://api.slack.com/apps/{app_id}")
            ui.message("Socket Mode: Basic Information → App-Level Tokens → connections:write.")
            ui.message("Bot: OAuth & Permissions → Bot User OAuth Token.")
        while settings.validation_error("SLACK_APP_TOKEN", values.get("SLACK_APP_TOKEN", "")):
            app_token = getpass.getpass("Socket Mode app token (xapp-…): ").strip()
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
            bot_token = getpass.getpass("Bot token (xoxb-…): ").strip()
            if error := settings.validation_error("SLACK_BOT_TOKEN", bot_token):
                ui.message(error)
                continue
            try:
                validate_slack_identity(bot_token, team_id=team_id, app_id=app_id, label="Bot token")
            except (slack_channels.SlackChannelError, RuntimeError) as exc:
                ui.message(str(exc))
                continue
            values = settings.update_config(config_path, {"SLACK_BOT_TOKEN": bot_token})
            break

    bot_identity = validate_slack_identity(
        values["SLACK_BOT_TOKEN"],
        team_id=values.get("SLACK_TEAM_ID", ""),
        app_id=values.get("SLACK_APP_ID", ""),
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

    ui.screen(3, "Where should Tag respond?", f"App {values.get('SLACK_APP_ID', '')} · Slack connected")
    if settings.validation_error("SLACK_ALLOWED_USER_IDS", values.get("SLACK_ALLOWED_USER_IDS", "")):
        owner_id = choose_allowed_users(values.get("SLACK_TEAM_ID", ""))
        values = settings.update_config(config_path, {"SLACK_ALLOWED_USER_IDS": owner_id})

    if not values.get("SLACK_CHANNEL_IDS") and values.get("SLACK_CHANNEL_ID"):
        values = settings.update_config(
            config_path, {"SLACK_CHANNEL_IDS": values["SLACK_CHANNEL_ID"]}
        )
    selected_channels: list[slack_channels.SlackChannel] = []
    def choose_setup_channels():
        return slack_permissions.recover(
            lambda: slack_channels.choose_channels(values["SLACK_BOT_TOKEN"], values.get("SLACK_CHANNEL_IDS", ""),
                                                  app_id=values.get("SLACK_APP_ID", "")),
            values.get("SLACK_APP_ID", ""),
        )

    if review_channels or settings.validation_error("SLACK_CHANNEL_IDS", values.get("SLACK_CHANNEL_IDS", "")):
        selected_channels = choose_setup_channels()
        values = settings.update_config(
            config_path,
            {"SLACK_CHANNEL_IDS": ",".join(channel.channel_id for channel in selected_channels)},
        )

    if not selected_channels:
        available = slack_permissions.recover(
            lambda: slack_channels.list_channels(values["SLACK_BOT_TOKEN"]), values.get("SLACK_APP_ID", ""),
        )
        visible = {channel.channel_id: channel for channel in available}
        selected_channels = [
            visible[channel_id]
            for channel_id in slack_channels.parse_channel_ids(values["SLACK_CHANNEL_IDS"])
            if channel_id in visible and visible[channel_id].is_member
        ]
    expected = set(slack_channels.parse_channel_ids(values["SLACK_CHANNEL_IDS"]))
    if {channel.channel_id for channel in selected_channels} != expected:
        ui.message("A saved channel is no longer available. Choose joined channels to continue.")
        selected_channels = choose_setup_channels()
        values = settings.update_config(config_path, {"SLACK_CHANNEL_IDS": ",".join(c.channel_id for c in selected_channels)})
    while True:
        print()
        ui.message("Selected: " + ", ".join(f"#{c.name}" for c in selected_channels))
        memory_label = "invited channels" if channel_policy == "invited" else "selected channels only"
        ui.message(f"Slack memory · last {values['MFS_SLACK_HISTORY_DAYS']} days · {memory_label}")
        if channel_policy == "invited":
            ui.message("Automatic memory: all channels Tag has joined, including future invitations.")
            ui.message("Anyone who can invite this app can enable that channel's history indexing.")
            ui.message("Membership is checked about every minute while Tag runs. Leaving stops future retrieval, not stored-data retention.")
        ui.message("Replies use the current channel’s memory only.")
        ui.message("Allowed callers: " + values["SLACK_ALLOWED_USER_IDS"] + " · channel members can see replies")
        ui.message("Agent: " + ("Codex" if values["OPENTAG_BACKEND"] == "codex" else "Claude · experimental"))
        choice = ui.choose("Ready to continue?", [
            f"Use {len(selected_channels)} channel(s) and finish setup",
            "Change channels", "Change defaults", "Save and exit",
        ])
        if choice == 3:
            raise ui.Paused()
        if choice == 1:
            selected_channels = choose_setup_channels()
            values = settings.update_config(config_path, {"SLACK_CHANNEL_IDS": ",".join(c.channel_id for c in selected_channels)})
        elif choice == 2:
            days = ("7", "30", "90")
            day = ui.choose("Slack history window", [f"Last {d} days" for d in days], default=days.index(values["MFS_SLACK_HISTORY_DAYS"]))
            agent = ui.choose("Agent", ["Codex · recommended", "Claude · experimental"], default=int(values["OPENTAG_BACKEND"] == "claude"))
            values = settings.update_config(config_path, {"MFS_SLACK_HISTORY_DAYS": days[day], "OPENTAG_BACKEND": ("codex", "claude")[agent]})
        else:
            ui.message("Continue will index the selected history and start Tag. No test message is sent." if start_services
                       else "Continue saves these choices only. No services or indexing will start.")
            if ui.choose("Approve setup", ["Continue", "Back"], default=1) == 0:
                if channel_policy == "invited":
                    values = settings.update_config(config_path, {"SLACK_CHANNEL_POLICY": channel_policy})
                break
    ui.screen(4, "Finishing setup", "Your Slack app and channel choices are saved.")
    ui.message("✓ Slack connected\n◌ Preparing Slack memory…")
    required_scopes = [connector_scope(values["SLACK_TEAM_ID"], channel) for channel in selected_channels]
    saved_scopes = [scope.strip() for scope in values.get("MFS_ALLOWED_SCOPES", "").split(",") if scope.strip()]
    saved_connector = Path(values.get("MFS_SLACK_CONNECTOR_CONFIG", ""))
    memory_incomplete = (
        not values.get("MFS_SLACK_TOKEN")
        or values.get("MFS_SLACK_CONNECTOR_URI") != f"slack://tag-{values['SLACK_TEAM_ID'].lower()}"
        or not saved_connector.is_file()
        or not set(required_scopes).issubset(saved_scopes)
        or saved_connector.read_text() != render_slack_connector(values["SLACK_TEAM_ID"], selected_channels, values["MFS_SLACK_HISTORY_DAYS"])
    )
    if memory_incomplete:
        history_token = values.get("MFS_SLACK_TOKEN") or values["SLACK_BOT_TOKEN"]
        while True:
            try:
                validate_slack_identity(history_token, team_id=values.get("SLACK_TEAM_ID", ""), label="Slack-history credential")
                for channel in selected_channels:
                    slack_channels.slack_api(history_token, "conversations.history", {"channel": channel.channel_id, "limit": "1"})
                break
            except (slack_channels.SlackChannelError, RuntimeError) as exc:
                ui.message(f"History access needs attention: {exc}")
                if isinstance(exc, slack_permissions.MissingScope):
                    slack_permissions.guidance(exc, values.get("SLACK_APP_ID", ""))
                action = ui.choose("Continue with saved channels", ["Check again", "Use a different history credential", "Save and exit"])
                if action == 2:
                    raise ui.Paused()
                if action == 1:
                    history_token = ask_secret("Slack-history token (hidden)", "xox")
        connector = write_slack_connector(
            values["SLACK_TEAM_ID"], selected_channels, values["MFS_SLACK_HISTORY_DAYS"], home=home
        )
        scopes = ",".join(dict.fromkeys([*saved_scopes, *required_scopes]))
        values = settings.update_config(
            config_path,
            {
                "MFS_SLACK_TOKEN": history_token,
                "MFS_ALLOWED_SCOPES": scopes,
                "MFS_SLACK_CONNECTOR_URI": f"slack://tag-{values['SLACK_TEAM_ID'].lower()}",
                "MFS_SLACK_CONNECTOR_CONFIG": str(connector),
            },
        )
        ui.message(f"Configured selected-channel Slack memory: {connector}")
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


def finish_setup(config_path: Path, values: dict[str, str], channels: list[slack_channels.SlackChannel]) -> int:
    ui.message("✓ Slack memory configured")
    backend = values["OPENTAG_BACKEND"]
    while not selected_backend_available(backend):
        if ui.choose("Agent needs installation", ["Check again", "Save and exit"]) == 1:
            raise ui.Paused()
    if backend == "codex":
        while subprocess.run([shutil.which("codex") or "codex", "login", "status"], capture_output=True).returncode:
            ui.message("Codex needs sign-in. Your Slack and memory choices are saved.")
            action = ui.choose("Sign in to continue", ["Open Codex sign-in", "Check again", "Save and exit"])
            if action == 2:
                raise ui.Paused()
            if action == 0:
                subprocess.run([shutil.which("codex") or "codex", "login"], check=False)
        ui.message("✓ Codex signed in · first task still unverified")
    else:
        ui.message("✓ Claude executable available · sign-in will be checked by its first task")
    environment = dict(os.environ, OPENTAG_ENV_FILE=str(config_path))
    while True:
        ui.message("◌ Starting memory and connecting Tag…")
        result = subprocess.run([sys.executable, str(ROOT / "scripts/tag_cli.py"), "start"], env=environment, text=True, capture_output=True)
        if result.returncode == 0:
            print()
            ui.message("✓ Tag is connected.")
            ui.message("MFS healthy · Slack connected")
            ui.message("In any selected channel (" + ", ".join(f"#{c.name}" for c in channels) + "), send:")
            print()
            ui.message(f"@{values.get('OPENTAG_BOT_NAME', 'OpenMax')} say hello", indent="    ")
            print()
            ui.message("First reply: not verified yet. Observe the reply in Slack.")
            return 0
        ui.message(safe_cli_output(result.stdout + "\n" + result.stderr))
        ui.message("Startup needs attention. Your completed setup is saved.")
        if ui.choose("After addressing the error", ["Check again", "Save and exit"]) == 1:
            raise ui.Paused()


def completed_setup_status(config_path: Path) -> int | None:
    """Completed onboarding is a health check, not another indexing approval."""
    progress = config_path.with_name("setup-progress.json")
    if progress.exists():
        state = slack_app_create.read_object(progress)
        if state.get("completed") is not True:
            return None
    values = settings.load_config(config_path)
    if (settings.config_errors(values) or not values.get("SLACK_CHANNEL_IDS")
            or not values.get("MFS_SLACK_TOKEN")
            or not Path(values.get("MFS_SLACK_CONNECTOR_CONFIG", "")).is_file()):
        return None
    try:
        from . import tag_control, tag_cli
    except ImportError:
        import tag_control, tag_cli
    report = tag_control.status_report(tag_home(), tag_cli)
    ui.message("Your setup is already saved. Checking readiness; no settings were changed.")
    tag_control.show_status(report)
    ui.message("Change choices with tag settings, or review with tag setup --review.")
    return int(report["state"] not in {"running", "stopped"})


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up Tag or resume missing configuration.")
    parser.add_argument("--config", type=Path, default=tag_home() / "config/settings.json", help="configuration file to create")
    parser.add_argument("--no-start", action="store_true", help="save setup choices without starting services or indexing history")
    parser.add_argument("--review", action="store_true", help="review completed setup choices")
    parser.add_argument("--review-channels", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--completion-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    config_path = args.config.expanduser().resolve()
    if not sys.stdin.isatty():
        print("Use a terminal for setup, or tag inspect --json and tag config set for automation.", file=sys.stderr)
        return 2
    try:
        os.environ["OPENTAG_ENV_FILE"] = str(config_path)
        if not args.review:
            completed = completed_setup_status(config_path)
            if completed is not None:
                return completed
        progress = config_path.with_name("setup-progress.json")
        settings.save_config(progress, {"completed": False})
        result = guided_setup(config_path, start_services=not args.no_start, review_channels=args.review_channels)
        if result == 0:
            settings.save_config(progress, {"completed": True, "services_requested": not args.no_start})
        if result == 0 and args.completion_file:
            settings.save_config(args.completion_file, {"approved": True})
        return result
    except ui.Paused:
        print()
        ui.message("✓ Progress saved. Run tag setup to continue.")
        return 0
    except (KeyboardInterrupt, EOFError):
        print()
        ui.message("Setup paused. Saved answers are kept; run tag setup to continue.")
        return 130
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Setup could not continue: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
