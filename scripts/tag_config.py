"""Validated, private settings shared by the CLI, setup, and menu."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

try:
    from mfs_scope_policy import canonical_uri, parse_scopes
except ImportError:
    from scripts.mfs_scope_policy import canonical_uri, parse_scopes

DEFAULTS = {
    "OPENTAG_BACKEND": "codex", "OPENTAG_BOT_NAME": "OpenMax",
    "OPENTAG_CODEX_TRANSPORT": "app-server",
    "OPENTAG_TRANSPORT": "slack", "OPENTAG_TIMEOUT_SECONDS": "420",
    "OPENTAG_BACKEND_ATTEMPTS": "3", "OPENTAG_SLACK_STREAMING": "1",
    "MFS_URL": "http://127.0.0.1:13619", "MFS_SLACK_HISTORY_DAYS": "30",
}
REQUIRED = ("OPENTAG_BACKEND", "MFS_URL", "MFS_ALLOWED_SCOPES",
            "SLACK_APP_TOKEN", "SLACK_BOT_TOKEN", "SLACK_ALLOWED_USER_IDS",
            "SLACK_TEAM_ID", "SLACK_APP_ID")
# Unknown extension settings are preserved but never exposed by config show.
PUBLIC = frozenset((*DEFAULTS, "MFS_ALLOWED_SCOPES", "OPENTAG_WORKDIR",
                    "SLACK_CHANNEL_ID", "SLACK_CHANNEL_IDS", "SLACK_TEAM_ID",
                    "SLACK_APP_ID", "SLACK_ALLOWED_USER_IDS",
                    "SLACK_CHANNEL_POLICY",
                    "MFS_SLACK_HISTORY_DAYS",
                    "MFS_SLACK_CONNECTOR_URI", "MFS_SLACK_CONNECTOR_CONFIG",
                    "OPENTAG_CODEX_MODELS", "OPENTAG_CODEX_REASONING_EFFORTS"))
EDITABLE = PUBLIC - {"OPENTAG_WORKDIR"} | {
    "SLACK_APP_TOKEN", "SLACK_BOT_TOKEN", "MFS_TOKEN", "MFS_SLACK_TOKEN", "MFS_HOME"
}
LABELS = {
    "OPENTAG_BACKEND": "Agent", "OPENTAG_BOT_NAME": "Bot name",
    "OPENTAG_CODEX_TRANSPORT": "Codex transport (exec or app-server)",
    "SLACK_APP_TOKEN": "Slack app token", "SLACK_BOT_TOKEN": "Slack bot token",
    "SLACK_ALLOWED_USER_IDS": "Who can use Tag", "SLACK_CHANNEL_ID": "Legacy channel restriction",
    "SLACK_CHANNEL_IDS": "Selected channels", "SLACK_TEAM_ID": "Slack workspace",
    "SLACK_CHANNEL_POLICY": "Channel policy (selected or invited)",
    "SLACK_APP_ID": "Slack app ID", "MFS_SLACK_TOKEN": "Slack history credential",
    "MFS_ALLOWED_SCOPES": "Allowed memory sources", "MFS_URL": "Memory server",
    "MFS_TOKEN": "Memory server token", "OPENTAG_TIMEOUT_SECONDS": "Task timeout (seconds)",
    "MFS_SLACK_HISTORY_DAYS": "Slack history window (days)",
    "MFS_SLACK_CONNECTOR_URI": "Slack history connector",
    "MFS_SLACK_CONNECTOR_CONFIG": "Slack history connector config",
    "OPENTAG_BACKEND_ATTEMPTS": "Retry attempts", "OPENTAG_SLACK_STREAMING": "Stream replies (1 on, 0 off)",
    "OPENTAG_TRANSPORT": "Chat service",
}


def config_path(home: Path) -> Path:
    return Path(os.getenv("OPENTAG_ENV_FILE", str(home / "config/settings.json"))).expanduser()


def read_config(path: Path) -> dict[str, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError):
        raise ValueError("Settings must contain valid JSON; repair the file before continuing") from None
    if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
        raise ValueError("TAG configuration must be a JSON object of string values")
    if any(not re.fullmatch(r"(?:OPENTAG|SLACK|MFS)_[A-Z0-9_]+", k) for k in value):
        raise ValueError("Configuration keys must start with OPENTAG_, SLACK_, or MFS_")
    return value


def load_config(path: Path) -> dict[str, str]:
    return read_config(path) if path.exists() else {}


def validation_error(key: str, value: str) -> str | None:
    if key in REQUIRED and not value.strip():
        return "Required"
    if "\x00" in value:
        return "Must not contain a null character"
    if key == "OPENTAG_BACKEND" and value not in {"codex", "claude"}:
        return "Choose codex or claude (experimental)"
    if key == "OPENTAG_CODEX_TRANSPORT" and value not in {"exec", "app-server"}:
        return "Choose exec or app-server"
    if key == "OPENTAG_TRANSPORT" and value != "slack":
        return "Only slack is supported"
    if key in {"OPENTAG_TIMEOUT_SECONDS", "OPENTAG_BACKEND_ATTEMPTS"} and (not value.isascii() or not value.isdigit() or int(value) < 1):
        return "Use a positive integer"
    if key == "MFS_SLACK_HISTORY_DAYS" and value not in {"7", "30", "90"}:
        return "Choose 7, 30, or 90 days"
    if key == "MFS_SLACK_CONNECTOR_URI" and value and not value.startswith("slack://"):
        return "Use a slack:// connector URI"
    if key == "MFS_SLACK_CONNECTOR_CONFIG" and value and not Path(value).is_absolute():
        return "Use an absolute connector configuration path"
    if key == "OPENTAG_SLACK_STREAMING" and value not in {"0", "1"}:
        return "Use 0 or 1"
    if key == "SLACK_CHANNEL_POLICY" and value not in {"selected", "invited"}:
        return "Choose selected or invited"
    if key in {"SLACK_APP_TOKEN", "SLACK_BOT_TOKEN", "MFS_SLACK_TOKEN"}:
        prefix = "xapp-" if key == "SLACK_APP_TOKEN" else "xox"
        if not value.startswith(prefix) or len(value) <= len(prefix) or any(c.isspace() for c in value):
            return f"Enter a valid {prefix}- token issued by Slack"
    if key == "SLACK_ALLOWED_USER_IDS" and not all(re.fullmatch(r"[UW][A-Z0-9]+", item.strip()) for item in value.split(",")):
        return "Use comma-separated Slack member IDs"
    if key == "SLACK_CHANNEL_ID" and value and not re.fullmatch(r"[CG][A-Z0-9]+", value):
        return "Use a Slack channel ID, or leave empty for no channel restriction"
    if key == "SLACK_CHANNEL_IDS":
        channel_ids = [item.strip() for item in value.split(",") if item.strip()]
        if not channel_ids or not all(re.fullmatch(r"[CG][A-Z0-9]+", item) for item in channel_ids):
            return "Select one or more comma-separated Slack channel IDs"
    if key == "SLACK_TEAM_ID" and value and not re.fullmatch(r"T[A-Z0-9]+", value):
        return "Use a Slack workspace Team ID"
    if key == "SLACK_APP_ID" and value and not re.fullmatch(r"A[A-Z0-9]+", value):
        return "Use a Slack App ID"
    if key == "MFS_URL":
        try:
            url = urlsplit(value)
            _ = url.port
            valid = url.scheme in {"http", "https"} and url.hostname and not (url.username or url.password or url.query or url.fragment)
        except ValueError:
            valid = False
        if not valid:
            return "Use an http(s) server URL without credentials, query, or fragment"
    if key == "MFS_ALLOWED_SCOPES":
        try:
            scopes = parse_scopes(value)
            valid = bool(scopes) and all(canonical_uri(scope) is not None for scope in scopes)
        except ValueError:
            valid = False
        if not valid:
            return "Use comma-separated source URIs, such as file://local/path or slack://team"
    return None


def config_errors(values: dict[str, str]) -> dict[str, str]:
    errors = {key: "Required" for key in REQUIRED if not values.get(key, "").strip()}
    if not (values.get("SLACK_CHANNEL_IDS", "").strip() or values.get("SLACK_CHANNEL_ID", "").strip()):
        errors["SLACK_CHANNEL_IDS"] = "Select at least one Slack channel"
    for key, value in values.items():
        if key in EDITABLE and (error := validation_error(key, value)):
            errors[key] = error
    return errors


def public_config(values: dict[str, str]) -> dict[str, str]:
    return {key: (value if key in PUBLIC and not validation_error(key, value)
                  else "[set]" if value else "[not set]") for key, value in values.items()}


def save_config(path: Path, values: dict[str, str]) -> None:
    """Atomic replace, with owner-only permissions before any secrets are written."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".settings-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(values, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def update_config(path: Path, changes: dict[str, str], *, only_missing: bool = False) -> dict[str, str]:
    """Apply only requested keys. A short lock prevents lost concurrent updates."""
    for key, value in changes.items():
        if key not in EDITABLE:
            raise ValueError("Unsupported setting; run tag config keys for editable settings")
        if error := validation_error(key, value):
            raise ValueError(f"{key}: {error}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = path.with_name(path.name + ".lock")
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise RuntimeError("Another settings update is in progress; retry after it finishes") from None
    os.close(descriptor)
    try:
        values = load_config(path)
        values.update({key: value for key, value in changes.items() if not only_missing or key not in values})
        save_config(path, values)
        return values
    finally:
        lock.unlink(missing_ok=True)
