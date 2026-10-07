"""Validated, private settings shared by the CLI, setup, and menu."""
from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

try:
    from agent_models import SUPPORTED_REASONING_EFFORTS
    from mfs_scope_policy import canonical_uri, parse_scopes
except ImportError:
    from scripts.agent_models import SUPPORTED_REASONING_EFFORTS
    from scripts.mfs_scope_policy import canonical_uri, parse_scopes

DEFAULTS = {
    "OPENTAG_BACKEND": "codex", "OPENTAG_BOT_NAME": "Tag",
    "OPENTAG_CODEX_TRANSPORT": "app-server",
    "OPENTAG_CLAUDE_TRANSPORT": "sdk", "OPENTAG_CLAUDE_PERMISSION_MODE": "auto",
    "OPENTAG_TRANSPORT": "slack", "OPENTAG_TIMEOUT_SECONDS": "420",
    "OPENTAG_MAX_TIMEOUT_SECONDS": "3600",
    "OPENTAG_BACKEND_ATTEMPTS": "3", "OPENTAG_SLACK_STREAMING": "1",
    "OPENTAG_SLACK_DM_ENABLED": "1",
    "OPENTAG_FILE_DELIVERY": "local+slack",
    "OPENTAG_THREAD_MAX_CONTEXT_TOKENS": "150000", "OPENTAG_THREAD_IDLE_HOURS": "4",
    "MFS_URL": "http://127.0.0.1:13619", "MFS_SLACK_HISTORY_DAYS": "30",
}
BACKENDS = frozenset({"codex", "claude"})
DEFAULT_MODEL_RE = re.compile(r"(?:codex|claude)(?::[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,99})?")
REQUIRED = ("OPENTAG_BACKEND", "MFS_URL", "MFS_ALLOWED_SCOPES",
            "SLACK_APP_TOKEN", "SLACK_BOT_TOKEN", "SLACK_ALLOWED_USER_IDS",
            "SLACK_TEAM_ID", "SLACK_APP_ID")
# Unknown extension settings are preserved but never exposed by config show.
PUBLIC = frozenset((*DEFAULTS, "MFS_ALLOWED_SCOPES", "OPENTAG_WORKDIR",
                    "SLACK_CHANNEL_ID", "SLACK_CHANNEL_IDS", "SLACK_TEAM_ID",
                    "SLACK_APP_ID", "SLACK_ALLOWED_USER_IDS", "SLACK_ENTERPRISE_ID",
                    "SLACK_CHANNEL_POLICY",
                    "MFS_SLACK_HISTORY_DAYS",
                    "MFS_SLACK_CONNECTOR_URI", "MFS_SLACK_CONNECTOR_CONFIG",
                    "OPENTAG_CODEX_MODELS", "OPENTAG_CODEX_REASONING_EFFORTS",
                    "OPENTAG_CLAUDE_MODELS", "OPENTAG_DEFAULT_MODEL", "OPENTAG_BACKENDS",
                    "OPENTAG_DEFAULT_EFFORT", "OPENTAG_BOT_DESCRIPTION"))
EDITABLE = PUBLIC - {"OPENTAG_WORKDIR"} | {
    "SLACK_APP_TOKEN", "SLACK_BOT_TOKEN", "MFS_TOKEN", "MFS_SLACK_TOKEN", "MFS_HOME"
}
LABELS = {
    "OPENTAG_BACKEND": "Agent", "OPENTAG_BOT_NAME": "Bot name",
    "OPENTAG_DEFAULT_MODEL": "Default model (codex:MODEL, claude:MODEL, or a backend)",
    "OPENTAG_DEFAULT_EFFORT": "Default thinking level",
    "OPENTAG_BOT_DESCRIPTION": "Description",
    "OPENTAG_BACKENDS": "Backends users can choose (codex,claude)",
    "OPENTAG_CODEX_TRANSPORT": "Codex transport (exec or app-server)",
    "OPENTAG_CLAUDE_TRANSPORT": "Claude transport (print or sdk)",
    "OPENTAG_CLAUDE_PERMISSION_MODE": "Claude permission mode",
    "SLACK_APP_TOKEN": "Slack app token", "SLACK_BOT_TOKEN": "Slack bot token",
    "SLACK_ALLOWED_USER_IDS": "Owners", "SLACK_CHANNEL_ID": "Legacy channel restriction",
    "SLACK_CHANNEL_IDS": "Selected channels", "SLACK_TEAM_ID": "Slack workspace",
    "SLACK_CHANNEL_POLICY": "Channel policy (selected or invited)",
    "SLACK_ENTERPRISE_ID": "Slack organization authorization",
    "SLACK_APP_ID": "Slack app ID", "MFS_SLACK_TOKEN": "Slack history credential",
    "MFS_ALLOWED_SCOPES": "Allowed memory sources", "MFS_URL": "Memory server",
    "MFS_TOKEN": "Memory server token", "OPENTAG_TIMEOUT_SECONDS": "Agent idle timeout (seconds)",
    "OPENTAG_MAX_TIMEOUT_SECONDS": "Maximum task runtime (seconds)",
    "MFS_SLACK_HISTORY_DAYS": "Slack history window (days)",
    "MFS_SLACK_CONNECTOR_URI": "Slack history connector",
    "MFS_SLACK_CONNECTOR_CONFIG": "Slack history connector config",
    "OPENTAG_BACKEND_ATTEMPTS": "Retry attempts", "OPENTAG_SLACK_STREAMING": "Stream replies (1 on, 0 off)",
    "OPENTAG_SLACK_DM_ENABLED": "Direct messages (1 on, 0 off)",
    "OPENTAG_FILE_DELIVERY": "File delivery (local or local+slack)",
    "OPENTAG_TRANSPORT": "Chat service",
    "OPENTAG_THREAD_MAX_CONTEXT_TOKENS": "Continue a Slack thread's conversation up to this many tokens (0 never continues)",
    "OPENTAG_THREAD_IDLE_HOURS": "Continue a Slack thread's conversation within this many idle hours (0 never continues)",
}


# API secrets stay outside PUBLIC; config show reports only whether they are set.
for _backend in ("CODEX", "CLAUDE"):
    for _suffix in ("AUTH", "BASE_URL"):
        PUBLIC |= {f"OPENTAG_{_backend}_{_suffix}"}
    EDITABLE |= {f"OPENTAG_{_backend}_{suffix}" for suffix in ("AUTH", "BASE_URL", "API_KEY")}
PUBLIC |= {"OPENTAG_CODEX_API_VERSION", "OPENTAG_MONTHLY_BUDGET_USD",
           "OPENTAG_CODEX_GATEWAY_FORMAT", "OPENTAG_CODEX_GATEWAY_PROVIDER",
            "OPENTAG_CODEX_GATEWAY_DISABLE_TOOLS",
           "OPENTAG_CODEX_INPUT_USD_PER_MILLION", "OPENTAG_CODEX_OUTPUT_USD_PER_MILLION",
           "OPENTAG_CODEX_CACHE_WRITE_USD_PER_MILLION", "OPENTAG_CODEX_CACHED_INPUT_USD_PER_MILLION"}
EDITABLE |= PUBLIC - {"OPENTAG_WORKDIR"}
EDITABLE |= {"OPENTAG_CODEX_API_VERSION"}
LABELS.update({
    "OPENTAG_CODEX_GATEWAY_DISABLE_TOOLS": "Gateway chat only: disable all tools (1 on, 0 off)",
    "OPENTAG_CODEX_AUTH": "Codex connection (inherit, api, azure)",
    "OPENTAG_CLAUDE_AUTH": "Claude connection (inherit, api)",
    "OPENTAG_CODEX_API_KEY": "Codex API key", "OPENTAG_CLAUDE_API_KEY": "Claude API key",
    "OPENTAG_CODEX_BASE_URL": "Codex API base URL", "OPENTAG_CLAUDE_BASE_URL": "Claude API base URL",
    "OPENTAG_CODEX_API_VERSION": "Azure API version (empty for v1)",
    "OPENTAG_CODEX_GATEWAY_FORMAT": "Gateway routing format (empty or provider.only)",
    "OPENTAG_CODEX_GATEWAY_PROVIDER": "Gateway provider ID (optional)",
    "OPENTAG_CODEX_MODELS": "Codex models or Azure deployment names (comma-separated)",
    "OPENTAG_CLAUDE_MODELS": "Claude API models (comma-separated)",
    "OPENTAG_MONTHLY_BUDGET_USD": "Monthly advisory budget (USD)",
    "OPENTAG_CODEX_INPUT_USD_PER_MILLION": "Codex uncached input (USD per million)",
    "OPENTAG_CODEX_OUTPUT_USD_PER_MILLION": "Codex output (USD per million)",
    "OPENTAG_CODEX_CACHE_WRITE_USD_PER_MILLION": "Codex cache writes (USD per million)",
    "OPENTAG_CODEX_CACHED_INPUT_USD_PER_MILLION": "Codex cached input (USD per million)",
})


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
    if (key.endswith("_USD_PER_MILLION") or key == "OPENTAG_MONTHLY_BUDGET_USD") and value:
        import math
        try:
            if not math.isfinite(float(value)) or float(value) < 0:
                return "Use a finite nonnegative dollar amount"
        except ValueError:
            return "Use a finite nonnegative dollar amount"
    if key in REQUIRED and not value.strip():
        return "Required"
    if "\x00" in value:
        return "Must not contain a null character"
    if key in {"OPENTAG_CODEX_AUTH", "OPENTAG_CLAUDE_AUTH"}:
        allowed = {"inherit", "api", "azure"} if "CODEX" in key else {"inherit", "api"}
        if value not in allowed:
            return "Choose " + ", ".join(sorted(allowed))
    if key == "OPENTAG_CODEX_GATEWAY_DISABLE_TOOLS" and value not in {"", "0", "1"}:
        return "Use 1 for chat only or 0 to enable tools"
    if key == "OPENTAG_CODEX_GATEWAY_FORMAT" and value not in {"", "provider.only"}:
        return "Use provider.only, or leave empty to disable routing"
    if key == "OPENTAG_CODEX_GATEWAY_PROVIDER" and value and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        return "Use one gateway provider ID (letters, numbers, underscores, dots, hyphens)"
    if key in {"OPENTAG_CODEX_BASE_URL", "OPENTAG_CLAUDE_BASE_URL"} and value:
        try:
            from .agent_connection import validate_url
        except ImportError:
            from agent_connection import validate_url
        try:
            validate_url(value)
        except ValueError as exc:
            return str(exc)
    if key.endswith("_API_KEY") and value and any(c.isspace() for c in value):
        return "API keys must not contain whitespace"
    if key == "OPENTAG_CODEX_API_VERSION" and value and not re.fullmatch(r"[A-Za-z0-9.-]{1,80}", value):
        return "Use the Azure API version without query syntax"
    if key == "OPENTAG_BACKEND" and value not in BACKENDS:
        return "Choose codex or claude"
    if key == "OPENTAG_DEFAULT_MODEL" and value and not DEFAULT_MODEL_RE.fullmatch(value):
        return "Use codex, claude, codex:MODEL, or claude:MODEL"
    if key == "OPENTAG_BACKENDS" and value and not set(value.split(",")) <= BACKENDS:
        return "Use a comma-separated list of codex and claude"
    if key == "OPENTAG_BOT_NAME" and (
        not value.strip()
        or len(value) > 35
        or any(unicodedata.category(character) in {"Cc", "Zl", "Zp"} for character in value)
    ):
        return "Use a name from 1 to 35 characters without line breaks"
    if key == "OPENTAG_BOT_DESCRIPTION" and (
        len(value) > 140
        or any(unicodedata.category(character) in {"Cc", "Zl", "Zp"} for character in value)
    ):
        return "Use one line of up to 140 characters"
    if key == "OPENTAG_DEFAULT_EFFORT" and value and value not in SUPPORTED_REASONING_EFFORTS:
        return "Choose " + ", ".join(SUPPORTED_REASONING_EFFORTS) + ", or leave empty for the model's default"
    if key == "OPENTAG_CODEX_TRANSPORT" and value not in {"exec", "app-server"}:
        return "Choose exec or app-server"
    if key == "OPENTAG_CLAUDE_TRANSPORT" and value not in {"print", "sdk"}:
        return "Choose print or sdk"
    if key == "OPENTAG_CLAUDE_PERMISSION_MODE" and value not in {"auto", "acceptEdits", "default", "dontAsk", "bypassPermissions"}:
        return "Choose auto, acceptEdits, default, dontAsk, or bypassPermissions"
    if key == "OPENTAG_FILE_DELIVERY" and value not in {"local", "local+slack"}:
        return "Choose local or local+slack"
    if key == "OPENTAG_TRANSPORT" and value != "slack":
        return "Only slack is supported"
    if key in {"OPENTAG_TIMEOUT_SECONDS", "OPENTAG_MAX_TIMEOUT_SECONDS", "OPENTAG_BACKEND_ATTEMPTS"} and (not value.isascii() or not value.isdigit() or int(value) < 1):
        return "Use a positive integer"
    if key in {"OPENTAG_THREAD_MAX_CONTEXT_TOKENS", "OPENTAG_THREAD_IDLE_HOURS"} and (not value.isascii() or not value.isdigit()):
        return "Use 0 or a positive integer"
    if key == "MFS_SLACK_HISTORY_DAYS" and value not in {"7", "30", "90"}:
        return "Choose 7, 30, or 90 days"
    if key == "MFS_SLACK_CONNECTOR_URI" and value and not value.startswith("slack://"):
        return "Use a slack:// connector URI"
    if key == "MFS_SLACK_CONNECTOR_CONFIG" and value and not Path(value).is_absolute():
        return "Use an absolute connector configuration path"
    if key in {"OPENTAG_SLACK_STREAMING", "OPENTAG_SLACK_DM_ENABLED"} and value not in {"0", "1"}:
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
    if key == "SLACK_ENTERPRISE_ID" and value and not re.fullmatch(r"E[A-Z0-9]+", value):
        return "Use a Slack organization ID starting with E"
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
    # A Tag that follows invitations may start with no channels, and so no
    # memory sources yet: it picks up each channel it's invited to while it runs.
    no_channels = not (values.get("SLACK_CHANNEL_IDS", "").strip() or values.get("SLACK_CHANNEL_ID", "").strip())
    follows_invitations = values.get("SLACK_CHANNEL_POLICY") == "invited"
    waiting = no_channels and follows_invitations
    errors = {key: "Required" for key in REQUIRED if not values.get(key, "").strip()
              and not (waiting and key == "MFS_ALLOWED_SCOPES")}
    if no_channels and not follows_invitations:
        errors["SLACK_CHANNEL_IDS"] = "Select at least one Slack channel"
    for key, value in values.items():
        if waiting and key in {"SLACK_CHANNEL_IDS", "MFS_ALLOWED_SCOPES"} and not value.strip():
            continue
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
        if key == "SLACK_CHANNEL_IDS" and not value.strip() and (
                changes.get("SLACK_CHANNEL_POLICY") or load_config(path).get("SLACK_CHANNEL_POLICY")) == "invited":
            continue  # A Tag that follows invitations may have no channels yet.
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
        align_default_backend(values, changes)
        save_config(path, values)
        return values
    finally:
        lock.unlink(missing_ok=True)


def align_default_backend(values: dict[str, str], changes: dict[str, str]) -> None:
    """Keep the default backend and the default model naming the same backend."""
    default_model = values.get("OPENTAG_DEFAULT_MODEL", "")
    model_backend = default_model.partition(":")[0]
    if "OPENTAG_DEFAULT_MODEL" in changes and model_backend in BACKENDS:
        values["OPENTAG_BACKEND"] = model_backend
    elif "OPENTAG_BACKEND" in changes and model_backend and model_backend != values.get("OPENTAG_BACKEND"):
        values["OPENTAG_DEFAULT_MODEL"] = ""


def migrate_file_delivery(home: Path, path: Path) -> bool:
    """Version 1: persist the new default without replacing an operator's choice."""
    marker = home / "state/migrations/file-delivery-v1.json"
    try:
        checkpoint = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        checkpoint = {}
    if isinstance(checkpoint, dict) and checkpoint.get("version") == "1":
        return False
    update_config(path, {"OPENTAG_FILE_DELIVERY": "local+slack"}, only_missing=True)
    saved = read_config(path)
    mode = saved.get("OPENTAG_FILE_DELIVERY", "")
    if mode not in {"local", "local+slack"}:
        raise RuntimeError(
            f"Invalid OPENTAG_FILE_DELIVERY value {mode!r}; "
            "run tag config set OPENTAG_FILE_DELIVERY local+slack"
        )
    # Atomic checkpoint comes last. Interrupted writes can be retried safely.
    save_config(marker, {"version": "1"})
    return True
