"""AI connections for one Tag: which agents can run, the default model, and sign-in.

Tag.app and the terminal share this module. ``tag [TAG] settings ai --json``
reports each backend's connection, ``models`` lists the signed-in accounts'
models grouped by backend, ``sign-in`` runs the browser sign-in with progress
and cancellation, and ``model`` saves the Tag's default model. Guided setup
asks the same questions through ``setup_step``.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import agent_models
    import setup_ui as ui
    import tag_chatgpt
    import tag_config as settings
    from opentag_process_env import without_telemetry_environment
    from tag_paths import default_workspace
except ImportError:
    from scripts import agent_models, setup_ui as ui, tag_chatgpt, tag_config as settings
    from scripts.opentag_process_env import without_telemetry_environment
    from scripts.tag_paths import default_workspace


ROOT = Path(__file__).resolve().parents[1]
SIGN_IN_TIMEOUT_SECONDS = 300
CHECK_TIMEOUT_SECONDS = 15
URL = re.compile(r"https://[^\s\"'<>]+")


@dataclass(frozen=True)
class Backend:
    key: str
    name: str
    provider: str
    install_url: str
    status_command: tuple[str, ...]
    login_command: tuple[str, ...]


BACKENDS = (
    Backend("codex", "Codex", "OpenAI models", "https://learn.chatgpt.com/docs/codex/cli",
            ("login", "status"), ("login",)),
    Backend("claude", "Claude", "Anthropic models", "https://code.claude.com/docs/en/setup",
            ("auth", "status", "--json"), ("auth", "login")),
)
BY_KEY = {backend.key: backend for backend in BACKENDS}
ACTION_LABELS = {
    "sign_in": "Sign in to {name}", "reconnect": "Reconnect {name}", "resume": "Resume {name}",
    "install": "Install {name}", "update": "Update {name}", "change_account": "Use another {name} account",
}
CODEX_METHOD_LABELS = {
    "chatgpt": "ChatGPT account for this Tag only",
    "codex": "Codex sign-in on this computer",
}


CLAUDE_SHARED = ("Claude's sign-in is shared with Claude Code on this computer. "
                 "Signing in with another account changes it there too.")


class SignInError(RuntimeError):
    """A sign-in that didn't finish; the message says what to do next."""


class SignInCancelled(SignInError):
    """The person cancelled; nothing changed."""


def _run(command: list[str], *, timeout: float = CHECK_TIMEOUT_SECONDS) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, env=without_telemetry_environment(os.environ),
                              check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _version(executable: str) -> str | None:
    result = _run([executable, "--version"], timeout=10)
    match = re.search(r"\d+\.\d+(?:\.\d+)?", result.stdout if result else "")
    return match.group(0) if match else None


def _connection(backend: Backend, state: str, *, installed: bool, version: str | None = None,
                method: str | None = None, account: str | None = None, detail: str = "",
                shared: bool = True, actions: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "backend": backend.key, "name": backend.name, "provider": backend.provider,
        "state": state, "installed": installed, "version": version, "method": method,
        "account": account, "detail": detail, "shared": shared,
        "actions": list(actions), "install_url": backend.install_url,
    }


def _codex_connection(home: Path, executable: str, version: str | None) -> dict[str, Any]:
    backend = BY_KEY["codex"]
    common = {"installed": True, "version": version}
    transport = "exec" if os.getenv("OPENTAG_CODEX_TRANSPORT") == "exec" else "app-server"
    supported = _run([executable, transport, "--help"])
    if supported is None or supported.returncode:
        return _connection(backend, "unsupported", detail=f"This Codex doesn't support {transport}. Update Codex.",
                           actions=("update",), **common)
    try:
        store = tag_chatgpt.Store(home)
        if store.enabled():
            account = store.status()["active_account"]
            email = (account or {}).get("email") or ""
            label = "ChatGPT plan · " + email if email else "ChatGPT plan"
            plan = {"method": "chatgpt", "account": label, "shared": False}
            if not account or not account["signed_in"]:
                return _connection(backend, "expired", detail="Sign in to ChatGPT again.",
                                   actions=("reconnect", "change_account"), **plan, **common)
            if not account["plan_enabled"]:
                return _connection(backend, "expired", detail="Allow Tag to use your ChatGPT plan.",
                                   actions=("reconnect", "change_account"), **plan, **common)
            if account["usage_paused"]:
                return _connection(backend, "limited", detail="Review usage at chatgpt.com/settings/usage, then resume.",
                                   actions=("resume", "change_account"), **plan, **common)
            return _connection(backend, "connected", actions=("change_account",), **plan, **common)
    except (tag_chatgpt.ChatGPTError, OSError, KeyError, TypeError):
        return _connection(backend, "expired", method="chatgpt", shared=False,
                           detail="Tag's ChatGPT account file needs attention. Sign in again.",
                           actions=("reconnect", "change_account"), **common)
    result = _run([executable, *backend.status_command])
    if result is None:
        return _connection(backend, "signed_out", detail="Couldn't check the Codex sign-in.",
                           actions=("sign_in",), method="codex", **common)
    if result.returncode == 0:
        text = (result.stdout + result.stderr).lower()
        account = "API key" if "api key" in text else "ChatGPT sign-in" if "chatgpt" in text else "Signed in"
        return _connection(backend, "connected", method="codex", account=account,
                           actions=("change_account",), **common)
    codex_home = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    if (codex_home / "auth.json").is_file():
        return _connection(backend, "expired", method="codex", detail="Sign in to Codex again.",
                           actions=("reconnect",), **common)
    return _connection(backend, "signed_out", method="codex", actions=("sign_in",), **common)


def _claude_account(status: dict[str, Any]) -> str:
    email = status.get("email") if isinstance(status.get("email"), str) else ""
    method = str(status.get("authMethod") or "")
    plan = str(status.get("subscriptionType") or "")
    if method == "claude.ai":
        name = f"Claude {plan.capitalize()}" if plan else "Claude account"
    elif method:
        name = "Anthropic Console" if "console" in method or "api" in method.lower() else method
    else:
        name = "Signed in"
    return f"{name} · {email}" if email else name


def _claude_previously_signed_in() -> bool:
    config_dir = Path(os.getenv("CLAUDE_CONFIG_DIR", str(Path.home()))).expanduser()
    try:
        record = json.loads((config_dir / ".claude.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(record, dict) and bool(record.get("oauthAccount"))


def _claude_connection(home: Path, executable: str, version: str | None) -> dict[str, Any]:
    backend = BY_KEY["claude"]
    common = {"installed": True, "version": version, "method": "claude"}
    if agent_models.rich_events_selected("claude"):
        import importlib.util

        if importlib.util.find_spec("claude_agent_sdk") is None:
            return _connection(backend, "unsupported", detail="Tag's Claude support isn't installed. Run tag upgrade.",
                               actions=("update",), **common)
    result = _run([executable, *backend.status_command])
    if result is None:
        return _connection(backend, "signed_out", detail="Couldn't check the Claude sign-in.",
                           actions=("sign_in",), **common)
    try:
        status = json.loads(result.stdout)
    except ValueError:
        status = {}
    status = status if isinstance(status, dict) else {}
    signed_in = status.get("loggedIn") is True if "loggedIn" in status else result.returncode == 0
    if signed_in:
        return _connection(backend, "connected", account=_claude_account(status),
                           actions=("change_account",), **common)
    if _claude_previously_signed_in():
        return _connection(backend, "expired", detail="Sign in to Claude again.", actions=("reconnect",), **common)
    return _connection(backend, "signed_out", actions=("sign_in",), **common)


def connection(home: Path, backend_key: str) -> dict[str, Any]:
    """Check one backend now: installed, supported, signed in, verified."""
    backend = BY_KEY[backend_key]
    executable = shutil.which(backend.key)
    if not executable:
        return _connection(backend, "not_installed", installed=False, actions=("install",))
    version = _version(executable)
    if backend.key == "codex":
        return _codex_connection(home, executable, version)
    return _claude_connection(home, executable, version)


def connections(home: Path) -> list[dict[str, Any]]:
    """Check every backend at once; each check runs the backend's own status command."""
    with ThreadPoolExecutor(max_workers=len(BACKENDS)) as pool:
        return list(pool.map(lambda backend: connection(home, backend.key), BACKENDS))


def allowed(values: dict[str, str]) -> list[str]:
    return agent_models.allowed_backends(values.get("OPENTAG_BACKEND") or "codex")


def usable(found: list[dict[str, Any]], values: dict[str, str]) -> list[str]:
    permitted = allowed(values)
    return [item["backend"] for item in found if item["state"] == "connected" and item["backend"] in permitted]


def default_choice(home: Path, values: dict[str, str], *, available: bool | None = None) -> dict[str, Any]:
    value = values.get("OPENTAG_DEFAULT_MODEL") or values.get("OPENTAG_BACKEND") or "codex"
    backend, model = agent_models.parse_model_choice(value, values.get("OPENTAG_BACKEND") or "codex")
    names = agent_models.load_model_names(agent_models.model_names_path(home))
    label = names.get(f"{backend}:{model}", model) if model else "Account default"
    return {"value": value, "backend": backend, "model": model, "label": label,
            "backend_name": agent_models.backend_display_name(backend), "available": available}


def report(home: Path, values: dict[str, str], *, tag_id: str, running: bool) -> dict[str, Any]:
    found = connections(home)
    permitted = allowed(values)
    for item in found:
        item["allowed"] = item["backend"] in permitted
    ready = usable(found, values)
    choice = default_choice(home, values)
    if choice["backend"] not in ready:
        choice["available"] = False
    return {"schema_version": 1, "tag": tag_id, "running": running, "connections": found,
            "usable": ready, "default_model": choice,
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


@contextmanager
def tag_environment(home: Path, values: dict[str, str]):
    """Run model discovery with this Tag's settings, then restore the process environment."""
    keys = ("OPENTAG_WORKDIR", "OPENTAG_BACKENDS", "OPENTAG_DEFAULT_MODEL", "OPENTAG_CODEX_MODELS",
            "OPENTAG_CLAUDE_MODELS", "OPENTAG_CLAUDE_TRANSPORT", "OPENTAG_CODEX_TRANSPORT")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update({key: values[key] for key in keys if values.get(key)})
    os.environ.setdefault("OPENTAG_WORKDIR", str(default_workspace(home)))
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def models(home: Path, values: dict[str, str], ready: list[str] | None = None) -> dict[str, Any]:
    """Each usable backend's live catalog, grouped by backend, and whether the default is offered.

    Every group starts with the account's own default (the bare backend), which
    is what a Tag without a chosen model uses.
    """
    if ready is None:
        ready = usable(connections(home), values)
    groups: list[dict[str, Any]] = []
    discovered: list[agent_models.ModelOption] = []
    with tag_environment(home, values):
        for name in [backend for backend in allowed(values) if backend in ready]:
            options = agent_models.discover_models(name)
            discovered.extend(options)
            entries = [{"value": name, "model": None, "label": "Account default", "default": False}]
            # Claude's placeholder for an unreported catalog is that same account default.
            entries += [{"value": option.value, "model": option.model_id, "label": option.label,
                         "default": option.is_default}
                        for option in options if option.model_id != "default"]
            groups.append({"backend": name, "name": agent_models.backend_display_name(name), "models": entries})
    if settings.config_path(home).is_file():
        agent_models.remember_model_names(discovered, agent_models.model_names_path(home))
    offered = {entry["value"] for group in groups for entry in group["models"]}
    choice = default_choice(home, values)
    choice["available"] = choice["value"] in offered
    choice["chosen"] = bool(values.get("OPENTAG_DEFAULT_MODEL"))
    unavailable = [{"backend": backend.key, "name": backend.name, "reason": "not_connected"}
                   for backend in BACKENDS if backend.key not in ready]
    return {"schema_version": 1, "groups": groups, "default": choice, "unavailable": unavailable,
            "suggested": suggested_default(groups, choice)}


def suggested_default(groups: list[dict[str, Any]], choice: dict[str, Any]) -> str | None:
    """Keep a chosen model that's still offered; otherwise the first account's own default model."""
    if choice.get("chosen") and choice["available"]:
        return choice["value"]
    for group in groups:
        marked = next((entry["value"] for entry in group["models"] if entry["default"]), None)
        return marked or group["backend"]
    return None


def save_default_model(home: Path, value: str, *, ready: list[str] | None = None,
                       values: dict[str, str] | None = None) -> dict[str, Any]:
    """Validate and save the Tag's default model; the backend follows the model."""
    if error := settings.validation_error("OPENTAG_DEFAULT_MODEL", value):
        raise ValueError(error)
    path = settings.config_path(home)
    values = values if values is not None else settings.load_config(path)
    backend, model = agent_models.parse_model_choice(value, values.get("OPENTAG_BACKEND") or "codex")
    if ready is None:
        ready = usable([connection(home, backend)], values)
    name = agent_models.backend_display_name(backend)
    if backend not in ready:
        raise ValueError(f"Connect {name} before choosing its models.")
    if model:
        names = agent_models.load_model_names(agent_models.model_names_path(home))
        if value not in names:
            offered = {entry["value"] for group in models(home, values, ready)["groups"] for entry in group["models"]}
            if value not in offered:
                raise ValueError(f"{model} isn't available from your connected {name} account. Pick another model.")
    saved = settings.update_config(path, {"OPENTAG_DEFAULT_MODEL": value})
    return default_choice(home, saved, available=True)


# ---- Sign-in ------------------------------------------------------------------

Emit = Callable[[dict[str, Any]], None]

STEP_TEXT = {
    "stopping": "Stopping {tag} while you sign in…",
    "browser": "Opening your browser…",
    "waiting": "Waiting for your browser…",
    "verifying": "Finishing sign-in…",
    "restarting": "Starting {tag} again…",
}


def _progress(emit: Emit, backend: str, step: str, tag: str = "Tag", **extra) -> None:
    emit({"type": "progress", "backend": backend, "step": step,
          "text": STEP_TEXT.get(step, step).format(tag=tag), **extra})


def _browser_login(command: list[str], backend: str, *, emit: Emit, cancelled: Callable[[], bool],
                   timeout: float) -> None:
    """Run a backend's own browser sign-in; relay its sign-in URL, stop it on cancel."""
    _progress(emit, backend, "browser")
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, errors="replace",
                                   env=without_telemetry_environment(os.environ))
    except OSError as exc:
        raise SignInError(f"Couldn't start sign-in: {exc}") from None
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    deadline = time.monotonic() + timeout
    reported_url = False
    output: list[str] = []
    _progress(emit, backend, "waiting")
    try:
        while True:
            if cancelled():
                raise SignInCancelled("Sign-in cancelled. Nothing changed.")
            if time.monotonic() > deadline:
                raise SignInError("Sign-in timed out. Try again when you're ready to finish it in your browser.")
            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                break
            output.append(line.strip())
            match = URL.search(line)
            if match and not reported_url:
                reported_url = True
                _progress(emit, backend, "waiting", url=match.group(0))
        code = process.wait(timeout=10)
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        raise
    if code:
        last = next((line for line in reversed(output) if line and not URL.search(line)), "")
        raise SignInError(f"{BY_KEY[backend].name} sign-in didn't finish{': ' + last if last else '.'}")


def sign_in(home: Path, backend_key: str, *, method: str | None = None, account: str | None = None,
            emit: Emit, cancelled: Callable[[], bool] = lambda: False,
            timeout: float = SIGN_IN_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Connect a backend through its browser sign-in and return the verified connection."""
    backend = BY_KEY.get(backend_key)
    if backend is None:
        raise SignInError("Choose codex or claude.")
    executable = shutil.which(backend.key)
    if not executable:
        raise SignInError(f"{backend.name} isn't installed. Install it from {backend.install_url}, then try again.")
    if backend.key == "codex":
        store = tag_chatgpt.Store(home)
        if method is None and store.enabled():
            # Signing in again without choosing another account renews the Tag's own one.
            method, account = "chatgpt", account or (store.status()["active_account"] or {}).get("id")
        method = method or "codex"
        if method not in CODEX_METHOD_LABELS:
            raise SignInError("Codex sign-in method must be chatgpt or codex.")
        if method == "chatgpt":
            if os.getenv("OPENTAG_CODEX_TRANSPORT") == "exec":
                raise SignInError("A ChatGPT plan needs the app-server transport. "
                                  "Run tag config set OPENTAG_CODEX_TRANSPORT app-server.")
            accounts = store.status(10000)["accounts"]
            current = next((row for row in accounts if row["id"] == account), None) if account else None
            try:
                store.login(account, consent=bool(current and not current["plan_enabled"]),
                            timeout=timeout, cancelled=cancelled,
                            progress=lambda step: _progress(emit, "codex", step))
            except tag_chatgpt.SignInCancelled:
                raise SignInCancelled("Sign-in cancelled. Nothing changed.") from None
            except tag_chatgpt.ChatGPTError as exc:
                raise SignInError(str(exc)) from None
        else:
            status = _run([executable, *backend.status_command])
            if store.enabled() and status is not None and status.returncode == 0:
                store.use_codex()  # Already signed in on this computer: just switch back.
            else:
                _browser_login([executable, *backend.login_command], "codex", emit=emit,
                               cancelled=cancelled, timeout=timeout)
                if store.enabled():
                    store.use_codex()
    else:
        _browser_login([executable, *backend.login_command], "claude", emit=emit,
                       cancelled=cancelled, timeout=timeout)
    _progress(emit, backend.key, "verifying")
    result = connection(home, backend.key)
    if result["state"] != "connected":
        raise SignInError(f"Sign-in finished, but {backend.name} still isn't ready: "
                          + (result["detail"] or result["state"].replace("_", " ")) + ".")
    return result


def resume(home: Path) -> dict[str, Any]:
    """Resume a paused ChatGPT plan after its usage limit was reviewed."""
    store = tag_chatgpt.Store(home)
    active = store.status()["active_account"]
    if not active:
        raise SignInError("No ChatGPT account is selected. Sign in again.")
    try:
        store.access(active["id"], activate=True)
    except tag_chatgpt.ChatGPTError as exc:
        raise SignInError(str(exc)) from None
    return connection(home, "codex")


class StdinCancel:
    """Cancel when the client sends {"cancel": true} or closes stdin (JSON-lines clients)."""

    def __init__(self) -> None:
        self.event = threading.Event()
        threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self) -> None:
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict) and message.get("cancel"):
                break
        self.event.set()

    def __call__(self) -> bool:
        return self.event.is_set()


# ---- Setup -----------------------------------------------------------------------

def _option(action: str, backend: str, method: str | None = None) -> tuple[str, str]:
    name = BY_KEY[backend].name
    if action == "change_account" and method:
        return f"change_account:{backend}:{method}", f"Change {name} account · {CODEX_METHOD_LABELS[method]}"
    return f"{action}:{backend}", ACTION_LABELS[action].format(name=name)


def connection_options(found: list[dict[str, Any]], values: dict[str, str]) -> list[tuple[str, str]]:
    ready = usable(found, values)
    options: list[tuple[str, str]] = []
    if ready:
        options.append(("continue", "Continue"))
    for item in found:
        if not item.get("allowed", True):
            continue
        for action in item["actions"]:
            if action == "change_account" and item["backend"] == "codex":
                options += [_option(action, "codex", method) for method in CODEX_METHOD_LABELS]
            else:
                options.append(_option(action, item["backend"]))
    options += [("check", "Check again"), ("exit", "Save and exit")]
    return options


def status_line(item: dict[str, Any]) -> str:
    state = item["state"]
    if state == "connected":
        return f"Connected · {item['account'] or 'signed in'}"
    label = {"signed_out": "Not signed in", "expired": "Sign-in expired", "limited": "Usage limit reached",
             "not_installed": "Not installed", "unsupported": "Update needed"}.get(state, state)
    return f"{label} · {item['detail']}" if item.get("detail") else label


def show_connections(found: list[dict[str, Any]]) -> None:
    for item in found:
        mark = "✓" if item["state"] == "connected" else "·"
        ui.message(f"{mark} {item['name']:<7} {status_line(item)}")


def _run_sign_in(home: Path, option: str, emit: Emit, cancelled: Callable[[], bool]) -> dict[str, Any]:
    """Perform one setup action; return a result event describing what happened."""
    action, _, rest = option.partition(":")
    backend, _, method = rest.partition(":")
    try:
        if action == "resume":
            item = resume(home)
        else:
            item = sign_in(home, backend, method=method or None, emit=emit, cancelled=cancelled)
        return {"type": "sign_in", "backend": backend, "status": "connected", "connection": item}
    except SignInCancelled as exc:
        return {"type": "sign_in", "backend": backend, "status": "cancelled", "error": str(exc), "retry": True}
    except SignInError as exc:
        return {"type": "sign_in", "backend": backend, "status": "failed", "error": str(exc), "retry": True}


def setup_step(home: Path, config_path: Path) -> dict[str, str]:
    """Guided setup's AI step: choose the Tag's default model.

    Connections are managed in Settings → AI & models. Setup lists them only
    while nothing usable is connected, because one is required; otherwise it
    asks for the model straight away and offers signing in to another agent as
    extra options. Over JSON lines both are ``choose`` questions with extra
    fields (``connections``, ``groups``, ``option_ids``), so older clients still
    show plain options.
    """
    values = settings.load_config(config_path)
    last: dict[str, Any] | None = None
    while True:
        found = connections(home)
        permitted = allowed(values)
        for item in found:
            item["allowed"] = item["backend"] in permitted
        ready = usable(found, values)
        if ready:
            picked = _choose_model(home, values, found, ready, last)
            if picked.startswith(("sign_in:", "reconnect:", "resume:")):
                last = _sign_in_from_setup(home, picked)
                continue
            saved = settings.update_config(config_path, {"OPENTAG_DEFAULT_MODEL": picked})
            ui.message("✓ Default model · " + agent_models.describe_model_choice(
                picked, saved.get("OPENTAG_BACKEND", "codex"),
                names=agent_models.load_model_names(agent_models.model_names_path(home))))
            return saved
        options = connection_options(found, values)
        print()
        ui.message("Tag works through Codex or Claude on this computer. Connect one to continue.")
        show_connections(found)
        ids = [option_id for option_id, _ in options]
        selected = ids[ui.choose(
            "Connect an AI", [label for _, label in options], qid="ai_connection",
            default=0, option_ids=ids, connections=found, can_continue=False,
            tag_name=values.get("OPENTAG_BOT_NAME") or "Tag",
            **({"last_result": last} if last else {}),
        )]
        last = None
        if selected == "check":
            continue
        if selected.startswith(("install:", "update:")):
            backend = BY_KEY[selected.split(":", 1)[1]]
            ui.message(f"Install or update {backend.name}: {backend.install_url}")
            ui.message("Then choose Check again.")
            continue
        last = _sign_in_from_setup(home, selected)


def _sign_in_from_setup(home: Path, selected: str) -> dict[str, Any]:
    if selected == "change_account:claude" and not ui.protocol_active():
        ui.message(CLAUDE_SHARED)
    with ui.client_cancellation() as cancelled:
        result = _run_sign_in(home, selected, ui.emit if ui.protocol_active() else _print_progress, cancelled)
    # A sign-in can't be replayed: Back stops here rather than opening the browser again.
    ui.commit()
    if ui.protocol_active():
        ui.emit(result)
    ui.message(f"✓ {BY_KEY[result['backend']].name} connected" if result["status"] == "connected" else result["error"])
    return result


def _print_progress(event: dict[str, Any]) -> None:
    if event.get("type") == "progress":
        ui.message(event["text"] + (f" {event['url']}" if event.get("url") else ""))


def _choose_model(home: Path, values: dict[str, str], found: list[dict[str, Any]], ready: list[str],
                  last: dict[str, Any] | None) -> str:
    """Ask for the default model; signing in to another agent is offered after the models."""
    ui.message("Loading models from your accounts…")
    catalog = models(home, values, ready)
    entries = [(entry["value"], f"{group['name']} · {entry['label']}")
               for group in catalog["groups"] for entry in group["models"]]
    if not entries:
        entries = [(backend, f"{agent_models.backend_display_name(backend)} · Account default") for backend in ready]
    if catalog["default"]["chosen"] and not catalog["default"]["available"]:
        ui.message(f"{catalog['default']['label']} isn't available from your connected accounts. Pick another.")
    more = [_option(action, item["backend"]) for item in found
            if item.get("allowed", True) and item["backend"] not in ready
            for action in item["actions"] if action in {"sign_in", "reconnect", "resume"}]
    ids = [value for value, _ in entries] + [option_id for option_id, _ in more]
    suggested = catalog["suggested"]
    choice = ui.choose(
        "Default model", [label for _, label in entries + more], qid="default_model",
        default=ids.index(suggested) if suggested in ids else 0, option_ids=ids,
        groups=catalog["groups"], connections=found, tag_name=values.get("OPENTAG_BOT_NAME") or "Tag",
        **({"last_result": last} if last else {}),
    )
    if ids[choice] not in {value for value, _ in more}:
        ui.message("Picking a model also picks its agent. Change it any time in tag settings.")
    return ids[choice]


# ---- Command line: tag [TAG] settings ai … -------------------------------------------

USAGE = ("tag [TAG] settings ai [status | models | sign-in codex|claude [--method chatgpt|codex] "
         "[--account ID] | resume | model VALUE] [--restart] [--json]")


@dataclass
class Target:
    """The Tag a command acts on, and how to restart it."""
    home: Path
    tag_id: str
    running: Callable[[], bool]
    restart: Callable[[str], int]
    name: str = "Tag"


def _restart_note(target: Target) -> str:
    return f"Restart {target.name} to use it." if target.running() else f"{target.name} uses it next time it starts."


def cli(arguments: list[str], target: Target, *, json_output: bool = False, restart: bool = False,
        method: str | None = None, account: str | None = None) -> int:
    action = arguments[0] if arguments else "status"
    values = settings.load_config(settings.config_path(target.home))
    if action == "status" and len(arguments) <= 1:
        result = report(target.home, values, tag_id=target.tag_id, running=target.running())
        if json_output:
            print(json.dumps(result, indent=2))
            return 0
        ui.display.header("AI & models", f"{target.name} · checked now")
        show_connections(result["connections"])
        choice = result["default_model"]
        ui.message(f"Default model · {choice['backend_name']} · {choice['label']}")
        if not result["usable"]:
            ui.message("No AI is connected. Run tag settings ai sign-in codex or claude.")
        return 0 if result["usable"] else 1
    if action == "models" and len(arguments) == 1:
        result = models(target.home, values)
        if json_output:
            print(json.dumps(result, indent=2))
        else:
            for group in result["groups"]:
                ui.message(group["name"])
                for entry in group["models"]:
                    ui.message(f"  {entry['value']}  {entry['label']}{' · default' if entry['value'] == result['default']['value'] else ''}")
            for item in result["unavailable"]:
                ui.message(f"{item['name']} · not connected")
        return 0
    if action == "model" and len(arguments) == 2:
        choice = save_default_model(target.home, arguments[1], values=values)
        running = target.running()
        restarted = bool(restart and running and target.restart("restart") == 0)
        result = {"schema_version": 1, "ok": True, "default_model": choice,
                  "restart_required": running and not restarted, "restarted": restarted}
        if json_output:
            print(json.dumps(result, indent=2))
        else:
            ui.message(f"✓ Default model · {choice['backend_name']} · {choice['label']}")
            ui.message("People who picked their own model in Slack keep it.")
            ui.message(f"{target.name} restarted." if restarted else _restart_note(target))
        return 0
    if action in {"sign-in", "resume"} and len(arguments) == (2 if action == "sign-in" else 1):
        backend = arguments[1] if action == "sign-in" else "codex"
        return _sign_in_command(target, backend, action, json_output=json_output, restart=restart,
                                method=method, account=account)
    raise ValueError("Use " + USAGE)


def _sign_in_command(target: Target, backend: str, action: str, *, json_output: bool, restart: bool,
                     method: str | None, account: str | None) -> int:
    if backend not in BY_KEY:
        raise ValueError("Choose codex or claude.")
    emit: Emit = (lambda event: print(json.dumps(event), flush=True)) if json_output else _print_progress
    cancelled: Callable[[], bool] = StdinCancel() if json_output else (lambda: False)
    running = target.running()
    # A ChatGPT plan belongs to the Tag and is read at start, so it can't change underneath it.
    needs_stop = backend == "codex" and (action == "resume" or method == "chatgpt" or tag_chatgpt.Store(target.home).enabled())
    if running and needs_stop and not restart:
        raise ValueError(f"Stop {target.name} before changing its Codex account, or add --restart "
                         "to stop it while you sign in and start it again afterwards.")
    stopped = False
    result: dict[str, Any]
    try:
        if running and restart:
            _progress(emit, backend, "stopping", target.name)
            if target.restart("stop"):
                raise SignInError(f"Couldn't stop {target.name}. Nothing changed.")
            stopped = True
        if action == "resume":
            item = resume(target.home)
        else:
            item = sign_in(target.home, backend, method=method, account=account, emit=emit, cancelled=cancelled)
        result = {"type": "sign_in", "backend": backend, "status": "connected", "connection": item}
    except KeyboardInterrupt:
        result = {"type": "sign_in", "backend": backend, "status": "cancelled",
                  "error": "Sign-in cancelled. Nothing changed.", "retry": True}
    except SignInCancelled as exc:
        result = {"type": "sign_in", "backend": backend, "status": "cancelled", "error": str(exc), "retry": True}
    except SignInError as exc:
        result = {"type": "sign_in", "backend": backend, "status": "failed", "error": str(exc), "retry": True}
    finally:
        if stopped:
            _progress(emit, backend, "restarting", target.name)
            restarted = target.restart("start") == 0
    if stopped:
        result["restarted"] = restarted
        if not restarted:
            result["error"] = (result.get("error", "") + f" {target.name} didn't start again; start it from Tag.").strip()
    result["restart_required"] = running and not stopped
    if json_output:
        print(json.dumps(result), flush=True)
    elif result["status"] == "connected":
        ui.message(f"✓ {BY_KEY[backend].name} connected · {result['connection']['account'] or 'signed in'}")
        if result["restart_required"]:
            ui.message(_restart_note(target))
    else:
        ui.message(result["error"])
    return 0 if result["status"] == "connected" else 1


# ---- Interactive settings section -----------------------------------------------------

def settings_menu(target: Target) -> None:
    """``tag settings`` → AI & models: the same choices Tag.app shows."""
    while True:
        values = settings.load_config(settings.config_path(target.home))
        result = report(target.home, values, tag_id=target.tag_id, running=target.running())
        ui.display.header("Settings / AI & models", target.name)
        show_connections(result["connections"])
        choice = result["default_model"]
        ui.message(f"Default model · {choice['backend_name']} · {choice['label']}"
                   + ("" if choice["available"] is not False else " · not available"))
        options = [("model", "Change default model")]
        for item in result["connections"]:
            if not item.get("allowed", True):
                continue
            for action in item["actions"]:
                if action == "change_account" and item["backend"] == "codex":
                    options += [_option(action, "codex", method) for method in CODEX_METHOD_LABELS]
                else:
                    options.append(_option(action, item["backend"]))
        options += [("check", "Check connections"), ("back", "Back")]
        selected = options[ui.choose("AI & models", [label for _, label in options])][0]
        if selected == "back":
            return
        if selected == "check":
            continue
        if selected == "model":
            if not result["usable"]:
                ui.message("Connect Codex or Claude first.")
                continue
            ui.message("Loading models from your accounts…")
            catalog = models(target.home, values, result["usable"])
            entries = [(entry["value"], f"{group['name']} · {entry['label']}")
                       for group in catalog["groups"] for entry in group["models"]]
            if not entries:
                ui.message("No connected account reported its models.")
                continue
            ids = [value for value, _ in entries]
            current = ids.index(choice["value"]) if choice["value"] in ids else 0
            picked = ui.choose("Default model", [label for _, label in entries] + ["Cancel"], default=current)
            if picked == len(entries):
                continue
            saved = save_default_model(target.home, ids[picked], ready=result["usable"], values=values)
            ui.message(f"✓ Default model · {saved['backend_name']} · {saved['label']}")
            ui.message("People who picked their own model in Slack keep it.")
            _offer_restart(target)
            continue
        if selected.startswith(("install:", "update:")):
            backend = BY_KEY[selected.split(":", 1)[1]]
            ui.message(f"Install or update {backend.name}: {backend.install_url}")
            continue
        action, _, rest = selected.partition(":")
        backend, _, method = rest.partition(":")
        if backend == "claude" and action == "change_account":
            ui.message(CLAUDE_SHARED)
        restart = False
        if target.running() and backend == "codex" and (method == "chatgpt" or action == "resume"
                                                        or tag_chatgpt.Store(target.home).enabled()):
            ui.message(f"{target.name} stops while you sign in and starts again afterwards.")
            restart = True
        try:
            _sign_in_command(target, backend, "resume" if action == "resume" else "sign-in", json_output=False,
                             restart=restart, method=method or None, account=None)
        except ValueError as exc:
            ui.message(str(exc))
            continue
        if not restart:
            _offer_restart(target)


def _offer_restart(target: Target) -> None:
    if not target.running():
        ui.message(f"Saved. {target.name} uses it next time it starts.")
        return
    if ui.choose(f"Restart {target.name} now to use it?", ["Restart now", "Later"]) == 0:
        ui.message(f"{target.name} restarted." if target.restart("restart") == 0
                   else f"{target.name} didn't restart. Run tag restart.")
