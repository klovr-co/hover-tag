"""A Tag's own API connection: ``tag [TAG] settings ai api set|clear|check``.

One command replaces the ordered ``tag config set OPENTAG_*`` calls in
docs/reference/api-connections.md. Tag.app and the terminal share it. Inputs are
checked before anything stops; settings are written in one atomic update; a
running Tag is stopped first and started again afterwards. If it can't start on
the new settings, the previous settings come back. The key is read from stdin
and never printed, logged, or put in an argument.
"""

from __future__ import annotations

import getpass
import importlib.util
import json
import shutil
import sys
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

try:
    import agent_connection
    import setup_ui as ui
    import tag_config as settings
except ImportError:
    from scripts import agent_connection, setup_ui as ui, tag_config as settings


# Which provider APIs each backend speaks. Azure OpenAI is Codex-only.
KINDS = {"codex": ("openai", "azure"), "claude": ("anthropic",)}
NAMES = {"codex": "Codex", "claude": "Claude"}
KIND_NAMES = {"openai": "OpenAI-compatible", "anthropic": "Anthropic-compatible", "azure": "Azure OpenAI"}
DEFAULT_URLS = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com"}
STEP_TEXT = {
    "checking": "Checking the connection…",
    "stopping": "Stopping {tag}…",
    "saving": "Saving the connection…",
    "restarting": "Starting {tag} again…",
    "restoring": "{tag} didn't start. Putting the previous connection back…",
}

Emit = Callable[[dict[str, Any]], None]


class ApiError(ValueError):
    pass


def _prefix(backend: str) -> str:
    return f"OPENTAG_{backend.upper()}_"


def _kind(backend: str, values: dict[str, str]) -> str:
    if agent_connection.mode(backend, values) == "azure":
        return "azure"
    return "openai" if backend == "codex" else "anthropic"


def host(url: str) -> str:
    try:
        return urlsplit(url).netloc or url
    except ValueError:
        return url


def summary(backend: str, values: dict[str, str]) -> dict[str, Any] | None:
    """What a Tag's API connection is, without its key; None when it uses the plan sign-in."""
    if not agent_connection.active(backend, values):
        return None
    prefix = _prefix(backend)
    kind = _kind(backend, values)
    url = values.get(prefix + "BASE_URL", "") or DEFAULT_URLS.get(kind, "")
    try:
        agent_connection.validate(backend, values)
        problem = ""
    except ValueError as exc:
        problem = str(exc)
    return {"backend": backend, "kind": kind, "kind_name": KIND_NAMES[kind], "base_url": url, "host": host(url),
            "models": agent_connection.models(backend, values),
            "api_version": values.get(prefix + "API_VERSION", "") if kind == "azure" else "",
            "key_set": bool(values.get(prefix + "API_KEY", "").strip()), "problem": problem}


def summaries(values: dict[str, str]) -> list[dict[str, Any]]:
    return [item for backend in KINDS if (item := summary(backend, values))]


def account(item: dict[str, Any]) -> str:
    """``API (gateway.example.com)``: what a connection row shows in place of a sign-in."""
    return f"{'Azure' if item['kind'] == 'azure' else 'API'} ({item['host']})"


def label(item: dict[str, Any]) -> str:
    """``Codex · API (gateway.example.com)``."""
    return f"{NAMES[item['backend']]} · {account(item)}"


def changes_for(backend: str, kind: str, base_url: str, model_list: list[str], api_version: str,
                key: str | None, values: dict[str, str]) -> dict[str, str]:
    """Validate everything first, so a bad input never stops a Tag.

    ``key`` is None before it has been read: the other inputs are checked first,
    so a mistake is reported before anyone pastes a key.
    """
    if backend not in KINDS:
        raise ApiError("Choose --backend codex or claude.")
    if kind not in KINDS[backend]:
        if kind == "azure":
            raise ApiError("Azure OpenAI works with Codex only. Use --backend codex.")
        raise ApiError(f"{NAMES[backend]} uses --kind "
                       + " or ".join(KINDS[backend]) + ".")
    if kind == "azure" and not base_url:
        raise ApiError("Azure needs --base-url, such as https://YOUR_RESOURCE.openai.azure.com/openai")
    if api_version and kind != "azure":
        raise ApiError("--api-version is only for --kind azure.")
    if not model_list:
        raise ApiError("List at least one model with --models"
                       + (" (your Azure deployment names)." if kind == "azure" else "."))
    prefix = _prefix(backend)
    saved_key = values.get(prefix + "API_KEY", "")
    if key == "" and not saved_key:
        raise ApiError("Enter the API key. Tag reads it from stdin, so it stays out of your shell history.")
    changes = {prefix + "BASE_URL": base_url, prefix + "MODELS": ",".join(model_list),
               prefix + "AUTH": "azure" if kind == "azure" else "api"}
    if key:
        changes[prefix + "API_KEY"] = key
    if backend == "codex":
        changes["OPENTAG_CODEX_API_VERSION"] = api_version if kind == "azure" else ""
    # A Tag on this agent keeps its model if the API serves it, else uses the first one.
    # A Tag on the other agent keeps it: the API replaces this agent's sign-in, nothing more.
    chosen, _, model = values.get("OPENTAG_DEFAULT_MODEL", "").partition(":")
    if (chosen or values.get("OPENTAG_BACKEND") or "codex") == backend and model not in model_list:
        changes["OPENTAG_DEFAULT_MODEL"] = f"{backend}:{model_list[0]}"
    for setting, value in changes.items():
        if error := settings.validation_error(setting, value):
            raise ApiError(f"{_field(setting)}: {error}")
    merged = {**values, **changes}
    if key is None and not saved_key:
        merged[prefix + "API_KEY"] = "not-read-yet"
    try:
        agent_connection.validate(backend, merged)
    except ValueError as exc:
        raise ApiError(str(exc)) from None
    return changes


def clear_changes(backend: str, values: dict[str, str]) -> dict[str, str]:
    """Back to the plan sign-in. The key is removed; the URL and models stay for next time."""
    if backend not in KINDS:
        raise ApiError("Choose --backend codex or claude.")
    prefix = _prefix(backend)
    changes = {prefix + "AUTH": "inherit", prefix + "API_KEY": ""}
    if backend == "codex":
        # Routing settings require an API connection; leaving them would stop the Tag starting.
        for setting in ("GATEWAY_FORMAT", "GATEWAY_PROVIDER", "GATEWAY_DISABLE_TOOLS"):
            if values.get(prefix + setting):
                changes[prefix + setting] = ""
    chosen, _, model = values.get("OPENTAG_DEFAULT_MODEL", "").partition(":")
    if chosen == backend and model and model in agent_connection.models(backend, values):
        # API-only model IDs may not exist on the plan; use the account's own default.
        changes["OPENTAG_DEFAULT_MODEL"] = backend
    return changes


def _field(setting: str) -> str:
    return {"BASE_URL": "Base URL", "MODELS": "Models", "API_KEY": "API key", "AUTH": "Connection",
            "API_VERSION": "API version"}.get(setting.split("_", 2)[-1], setting)


def check(backend: str, values: dict[str, str]) -> list[dict[str, Any]]:
    """The doctor's checks for one backend. Nothing is sent to the provider, so no tokens are spent."""
    checks = []
    try:
        agent_connection.validate(backend, values)
        configured = agent_connection.active(backend, values)
        checks.append({"name": "configuration", "ok": True,
                       "text": "Configured. The key and models are checked on the first real request."
                       if configured else "Uses the plan sign-in, not an API."})
    except ValueError as exc:
        checks.append({"name": "configuration", "ok": False, "text": str(exc)})
    found = shutil.which(backend) is not None
    checks.append({"name": "agent", "ok": found,
                   "text": f"{'Codex' if backend == 'codex' else 'Claude Code'} is "
                           + ("installed." if found else "not installed.")})
    if backend == "claude":
        sdk = importlib.util.find_spec("claude_agent_sdk") is not None
        checks.append({"name": "sdk", "ok": sdk,
                       "text": "Claude support is installed." if sdk else "Claude support is missing. Run tag upgrade."})
    return checks


def read_key(json_output: bool) -> str:
    """One line from stdin: the raw key, or ``{"api_key": "…"}`` from Tag.app. Empty keeps the saved key."""
    if not json_output and sys.stdin.isatty():
        return getpass.getpass("API key (leave empty to keep the saved key): ").strip()
    line = sys.stdin.readline().strip()
    if line.startswith("{"):
        try:
            message = json.loads(line)
        except ValueError:
            raise ApiError("Send the key as {\"api_key\": \"…\"}.") from None
        line = str(message.get("api_key") or "") if isinstance(message, dict) else ""
    return line.strip()


def _in_use(backend: str, values: dict[str, str]) -> bool:
    """Whether the Tag's default model runs on this agent, so its requests use this connection."""
    chosen = values.get("OPENTAG_DEFAULT_MODEL", "").partition(":")[0] or values.get("OPENTAG_BACKEND") or "codex"
    return chosen == backend


def _progress(emit: Emit, step: str, target) -> None:
    emit({"type": "progress", "step": step, "text": STEP_TEXT[step].format(tag=target.name)})


def apply(target, backend: str, action: str, changes: dict[str, str], *, emit: Emit, restart: bool) -> dict[str, Any]:
    """Stop, write in one atomic update, start; restore the old settings if the Tag can't start."""
    path = settings.config_path(target.home)
    before = settings.load_config(path)
    # Saving what's already saved, or clearing what's already clear, doesn't restart the Tag.
    # An unset connection setting already means inherit.
    changes = {key: value for key, value in changes.items()
               if before.get(key, "inherit" if key.endswith("_AUTH") else "") != value}
    if not changes:
        return {"type": "api", "action": action, "backend": backend, "status": "saved",
                "api": summary(backend, before), "in_use": _in_use(backend, before), "default_model": before.get("OPENTAG_DEFAULT_MODEL", ""),
                "restarted": False, "restart_required": False, "unchanged": True}
    running = target.running()
    if running and not restart:
        raise ApiError(f"Stop {target.name} before changing its API connection, or add --restart "
                       "to stop it now and start it again afterwards.")
    result: dict[str, Any] = {"type": "api", "action": action, "backend": backend}
    if running:
        _progress(emit, "stopping", target)
        if target.restart("stop"):
            target.restart("start")
            raise ApiError(f"Couldn't stop {target.name}. Nothing changed.")
    _progress(emit, "saving", target)
    try:
        settings.update_config(path, changes)
    except (ValueError, RuntimeError, OSError) as exc:
        if running:
            target.restart("start")
        raise ApiError(f"Couldn't save the connection: {exc} Nothing changed.") from None
    restarted = False
    if running:
        _progress(emit, "restarting", target)
        restarted = target.restart("start") == 0
        if not restarted:
            _progress(emit, "restoring", target)
            # Exactly the previous file: nothing else writes settings while the Tag is stopped here.
            settings.save_config(path, before)
            back = target.restart("start") == 0
            raise ApiError(f"{target.name} didn't start with the new connection, so the previous one was kept"
                           + ("." if back else f", but {target.name} didn't start either. Start it from Tag.")
                           + " Run tag doctor for details.")
    saved = settings.load_config(path)
    result.update({"status": "saved", "api": summary(backend, saved), "in_use": _in_use(backend, saved),
                   "default_model": saved.get("OPENTAG_DEFAULT_MODEL", ""),
                   "restarted": restarted, "restart_required": False, "unchanged": False})
    return result


USAGE = ("tag [TAG] settings ai api [status] | set --backend codex|claude --kind openai|anthropic|azure "
         "[--base-url URL] --models A,B [--api-version V] | clear --backend codex|claude | "
         "check --backend codex|claude [--restart] [--json]; the key is read from stdin")


def cli(arguments: list[str], target, *, json_output: bool, restart: bool, backend: str | None = None,
        kind: str | None = None, base_url: str | None = None, models: str | None = None,
        api_version: str | None = None) -> int:
    action = arguments[0] if arguments else "status"
    if len(arguments) > 1 or action not in {"status", "set", "clear", "check"}:
        raise ApiError("Use " + USAGE)
    if action != "set" and (kind or base_url is not None or models is not None or api_version is not None):
        raise ApiError("--kind, --base-url, --models and --api-version are only for tag settings ai api set")
    path = settings.config_path(target.home)
    values = settings.load_config(path)
    if action == "status":
        result = {"schema_version": 1, "tag": target.tag_id, "running": target.running(),
                  "api": summaries(values), "kinds": {key: list(kinds) for key, kinds in KINDS.items()}}
        if json_output:
            print(json.dumps(result, indent=2))
        else:
            ui.display.header("API connections", target.name)
            for item in result["api"] or []:
                ui.message(f"{label(item)} · {', '.join(item['models'])}" + (f" · {item['problem']}" if item["problem"] else ""))
            if not result["api"]:
                ui.message("Uses your plan sign-in. Add an API: tag settings ai api set --help")
        return 0
    if backend is None:
        raise ApiError(f"Add --backend codex or claude to tag settings ai api {action}")
    if action == "check":
        checks = check(backend, values)
        ok = all(item["ok"] for item in checks)
        result = {"schema_version": 1, "type": "api", "action": "check", "backend": backend, "ok": ok,
                  "checks": checks, "api": summary(backend, values)}
        if json_output:
            print(json.dumps(result))
        else:
            for item in checks:
                ui.message(("✓ " if item["ok"] else "✗ ") + item["text"])
        return 0 if ok else 1
    emit: Emit = (lambda event: print(json.dumps(event), flush=True)) if json_output else (lambda event: ui.message(event["text"]))
    try:
        if action == "set":
            model_list = list(dict.fromkeys(m.strip() for m in (models or "").split(",") if m.strip()))
            inputs = (backend, kind or "", (base_url or "").strip(), model_list, (api_version or "").strip())
            changes_for(*inputs, None, values)  # Check the visible inputs before asking for the key.
            changes = changes_for(*inputs, read_key(json_output), values)
        else:
            changes = clear_changes(backend, values)
        result = apply(target, backend, action, changes, emit=emit, restart=restart)
    except ApiError as exc:
        result = {"type": "api", "action": action, "backend": backend, "status": "failed", "error": str(exc)}
    if json_output:
        print(json.dumps(result), flush=True)
    elif result["status"] == "saved":
        ui.message(f"✓ {label(result['api'])} · {', '.join(result['api']['models'])}" if result["api"]
                   else f"✓ {NAMES[backend]} uses your plan sign-in again.")
        if result["api"] and not result["in_use"]:
            ui.message(f"{target.name} uses another agent by default. To use this API, choose a "
                       f"{NAMES[backend]} model: tag settings ai model {backend}:{result['api']['models'][0]}")
        if not result["restarted"] and not target.running():
            ui.message(f"{target.name} uses it next time it starts.")
    else:
        ui.message(result["error"])
    return 0 if result["status"] == "saved" else 1
