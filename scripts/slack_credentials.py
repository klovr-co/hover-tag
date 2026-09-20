"""Receive credentials via Slack's documented deploy hook, without running Tag."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

try:
    import slack_app_create
    import tag_config as settings
except ImportError:
    from scripts import slack_app_create, tag_config as settings

ROOT = Path(__file__).resolve().parents[1]


class ConnectionFailure(RuntimeError):
    def __init__(self, title: str, detail: str, code: str = ""):
        self.title, self.detail, self.code = title, detail, code
        super().__init__(f"{title}{f' ({code})' if code else ''}. {detail}")


def handoff_failure(output: str) -> ConnectionFailure:
    """Classify known failures without exposing any captured CLI text."""
    if (re.search(r"(?<![\w-])service_limits_exceeded(?![\w-])", output)
            and "10 apps limit for free teams" in output.lower()):
        return ConnectionFailure(
            "Slack's free-workspace limit of 10 apps was reached",
            "In Slack → Apps → Manage apps, uninstall an unused app, then retry. "
            "Ask a workspace admin if needed. A paid plan is another option. "
            "Tag won't remove apps or change your plan.",
            "service_limits_exceeded",
        )
    messages = {
        "service_limits_exceeded": "Ask your workspace admin or Slack support to resolve the limit, then retry. Slack did not specify which limit.",
        "invalid_auth": "Slack CLI authorization is invalid. Run slack login in your terminal, then retry.",
        "token_expired": "Slack CLI authorization expired. Run slack login in your terminal, then retry.",
        "token_revoked": "Slack CLI authorization was revoked. Run slack login in your terminal, then retry.",
        "missing_scope": (
            "Slack rejected the installation refresh: missing_scope. Review the required permissions "
            "in Slack app settings or ask your workspace admin. Tag will not repair permissions."
        ),
    }
    for code, message in messages.items():
        if re.search(r"(?<![\w-])" + re.escape(code) + r"(?![\w-])", output):
            title = "Slack couldn't connect — a service limit was reached" if code == "service_limits_exceeded" else "Slack couldn't connect"
            return ConnectionFailure(title, message, code)
    return ConnectionFailure(
        "Slack did not provide credentials",
        "Check installation approval and Slack CLI sign-in before retrying. "
        "You can also enter existing tokens privately.",
    )


def receive(project: Path, team_id: str, app_id: str) -> dict[str, str]:
    """Call only after operator approval of installation/credential refresh."""
    if not re.fullmatch(r"T[A-Z0-9]+", team_id) or not re.fullmatch(r"A[A-Z0-9]+", app_id):
        raise RuntimeError("Select a valid Slack workspace and app before connecting.")
    if app_id not in slack_app_create.saved_app_ids(project, team_id):
        raise RuntimeError("The selected app link could not be confirmed. No connection was attempted.")
    slack = shutil.which("slack")
    if not slack:
        raise RuntimeError("Slack CLI is required for automatic connection.")
    # No original hooks, manifests, .env, or unrelated app records are copied.
    with tempfile.TemporaryDirectory(prefix="tag-slack-handoff-") as directory:
        handoff = Path(directory)
        destination = handoff / "credentials.json"
        command = [str(Path(sys.executable).resolve()), str(ROOT / "scripts/slack_credential_hook.py")]
        hook = subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)
        settings.save_config(handoff / ".slack/config.json", {"manifest": {"source": "remote"}})
        settings.save_config(handoff / ".slack/hooks.json", {"hooks": {"deploy": hook}})
        settings.save_config(handoff / ".slack/apps.json", {
            "apps": {team_id: {"team_id": team_id, "app_id": app_id}}
        })
        environment = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("SLACK_", "OPENTAG_", "MFS_", "TAG_SLACK_"))
        }
        environment["TAG_SLACK_HANDOFF_FILE"] = str(destination)
        try:
            result = subprocess.run(
                [slack, "deploy", "--team", team_id, "--app", app_id,
                 "--hide-triggers", "--skip-update", "--no-color"],
                cwd=handoff, env=environment, input="", capture_output=True, text=True,
                check=False, timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError("Slack CLI could not complete the connection. Check authorization and try again.") from None
        # Never display CLI output: deployment wording is misleading for this no-op
        # hook, and diagnostics might contain sensitive values. No receipt means no success.
        if result.returncode or not destination.is_file():
            raise handoff_failure((result.stdout or "") + "\n" + (result.stderr or ""))
        try:
            credentials = slack_app_create.read_object(destination)
            if set(credentials) != {"SLACK_APP_TOKEN", "SLACK_BOT_TOKEN"}:
                raise ValueError()
            if any(not isinstance(value, str) or settings.validation_error(key, value)
                   for key, value in credentials.items()):
                raise ValueError()
            if not credentials["SLACK_BOT_TOKEN"].startswith("xoxb-"):
                raise ValueError()
        except (ValueError, RuntimeError):
            raise RuntimeError("Slack's credential handoff was incomplete. No credentials were saved.") from None
        return credentials
