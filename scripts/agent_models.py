"""Backend-neutral model discovery shared by the Slack bridge and Tag's CLI.

Each Tag has a default backend and model. Users may choose any model from the
backends that are allowed for the Tag and signed in on this machine; a choice
is written as ``backend:model``.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


try:
    from . import agent_connection
except ImportError:
    import agent_connection


SUPPORTED_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max", "ultra")
DEFAULT_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")


def default_workdir() -> Path:
    return Path(os.getenv("OPENTAG_WORKDIR", str(Path.cwd())))


@dataclass(frozen=True)
class ModelOption:
    model_id: str
    label: str
    reasoning_efforts: tuple[str, ...]
    supports_fast_mode: bool = False
    default_reasoning_effort: str | None = None
    is_default: bool = False
    default_fast_mode: bool = False
    backend: str = "codex"
    # The concrete model an alias such as ``opus`` resolves to, when reported.
    resolved_model: str | None = None

    @property
    def value(self) -> str:
        return f"{self.backend}:{self.model_id}"


CodexModelOption = ModelOption
BACKEND_NAMES = {"codex": "Codex", "claude": "Claude"}
# Claude reports supported effort levels but not a default; this is its documented default.
CLAUDE_DEFAULT_EFFORT = "high"


def backend_display_name(backend: str) -> str:
    return BACKEND_NAMES.get(backend, "Agent")


def codex_transport() -> str:
    return os.getenv("OPENTAG_CODEX_TRANSPORT", "app-server").strip().lower()


def claude_transport() -> str:
    return os.getenv("OPENTAG_CLAUDE_TRANSPORT", "sdk").strip().lower()


def rich_events_selected(backend: str) -> bool:
    """Whether the backend transport reports phased answers, activity, and approvals."""
    if backend == "codex":
        return codex_transport() == "app-server"
    if backend == "claude":
        return claude_transport() == "sdk"
    return False


def parse_model_choice(value: str, default_backend: str) -> tuple[str, str | None]:
    """Split ``backend:model``; bare values from older forms belong to the default backend."""
    backend, separator, model = value.partition(":")
    if separator and backend in BACKEND_NAMES:
        return backend, (model or None)
    if value in BACKEND_NAMES:
        return value, None
    return default_backend, (value or None)


def codex_models_cache_path() -> Path:
    codex_home = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    return codex_home / "models_cache.json"


def tag_codex_config_path() -> Path:
    """Return the project-local Codex configuration owned by Tag."""
    workdir = Path(os.getenv("OPENTAG_WORKDIR", str(Path.cwd()))).expanduser()
    return workdir / ".codex" / "config.toml"


def read_codex_config(path: Path) -> dict[str, Any]:
    try:
        try:
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 runtime
            import tomli as tomllib
        with path.open("rb") as handle:
            config = tomllib.load(handle)
        return config if isinstance(config, dict) else {}
    except (OSError, TypeError, ValueError):
        return {}


def configured_codex_defaults() -> tuple[str | None, str | None, bool]:
    """Layer Tag's model defaults over the user's global Codex defaults."""
    global_config = read_codex_config(codex_models_cache_path().with_name("config.toml"))
    tag_config = read_codex_config(tag_codex_config_path())

    def layered_value(key: str, validator: Callable[[Any], bool]) -> Any:
        local = tag_config.get(key)
        if validator(local):
            return local
        global_value = global_config.get(key)
        return global_value if validator(global_value) else None

    model = layered_value("model", lambda value: isinstance(value, str) and bool(value))
    effort = layered_value(
        "model_reasoning_effort",
        lambda value: isinstance(value, str) and value in SUPPORTED_REASONING_EFFORTS,
    )
    service_tier = layered_value(
        "service_tier",
        lambda value: isinstance(value, str)
        and value in {"default", "fast", "priority"},
    )
    return (
        model,
        effort,
        service_tier in {"fast", "priority"},
    )


def fetch_codex_model_catalog() -> list[dict[str, Any]] | None:
    """Use the installed Codex account's catalog, not another client's shared cache."""
    try:
        from .codex_agent_backend import CodexAppServer, CodexAppServerError
    except ImportError:
        from codex_agent_backend import CodexAppServer, CodexAppServerError
    try:
        from . import tag_chatgpt
    except ImportError:
        import tag_chatgpt
    if tag_chatgpt.enabled():
        try:
            return tag_chatgpt.models()
        except tag_chatgpt.ChatGPTError as exc:
            logging.getLogger(__name__).warning("ChatGPT model discovery unavailable: %s", exc)
            # Keep the bridge available without borrowing another account's catalog.
            # Each task still validates its selected account before inference.
            return []
    try:
        return CodexAppServer(["codex", "app-server"], cwd=default_workdir(), timeout=10).model_catalog()
    except (CodexAppServerError, OSError, ValueError):
        return None


def discover_codex_models() -> list[CodexModelOption]:
    """Prefer live account metadata; cached ordering never establishes a default."""
    configured = [
        value.strip()
        for value in os.getenv("OPENTAG_CODEX_MODELS", "").split(",")
        if value.strip()
    ]
    configured_model, configured_effort, configured_fast_mode = configured_codex_defaults()
    try:
        from . import tag_chatgpt
    except ImportError:
        import tag_chatgpt
    plan_connection = tag_chatgpt.enabled()
    if plan_connection:
        configured_fast_mode = False
    discovered: dict[str, CodexModelOption] = {}
    live_models = fetch_codex_model_catalog()
    try:
        if live_models is not None:
            payload = {"models": [{
                "slug": item.get("model"), "display_name": item.get("displayName"),
                "visibility": "hide" if item.get("hidden") else "list",
                "is_default": item.get("isDefault") is True,
                "default_reasoning_level": item.get("defaultReasoningEffort"),
                "additional_speed_tiers": item.get("additionalSpeedTiers", []),
                "supported_reasoning_levels": [
                    {"effort": level.get("reasoningEffort")}
                    for level in (item.get("supportedReasoningEfforts")
                                  if isinstance(item.get("supportedReasoningEfforts"), list) else [])
                    if isinstance(level, dict)
                ],
            } for item in live_models]}
        else:
            payload = json.loads(codex_models_cache_path().read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            payload = {}
        raw_models = [
            raw_model
            for raw_model in payload.get("models", [])
            if isinstance(raw_model, dict)
            and raw_model.get("visibility") != "hide"
            and isinstance(raw_model.get("slug"), str)
            and raw_model.get("slug")
        ]
        fallback_default = next((item["slug"] for item in raw_models
                                 if live_models is not None and item.get("is_default")), None)
        if plan_connection:
            available = {item["slug"] for item in raw_models}
            configured = [name for name in configured if name in available]
            if configured_model not in available:
                configured_model = None
            # The direct catalog guarantees display order, not isDefault. Choose
            # its first visible entry as Tag's default, without claiming entitlement.
            fallback_default = raw_models[0]["slug"] if raw_models else None
        default_model = configured_model or fallback_default
        for raw_model in raw_models:
            if not isinstance(raw_model, dict) or raw_model.get("visibility") == "hide":
                continue
            model_id = raw_model.get("slug")
            if not isinstance(model_id, str) or not model_id:
                continue
            efforts = tuple(
                item["effort"]
                for item in raw_model.get("supported_reasoning_levels", [])
                if isinstance(item, dict) and isinstance(item.get("effort"), str)
            )
            speed_tiers = raw_model.get("additional_speed_tiers", [])
            if not isinstance(speed_tiers, list):
                speed_tiers = []
            catalog_effort = raw_model.get("default_reasoning_level")
            is_default = model_id == default_model
            default_effort = configured_effort if is_default and configured_effort else catalog_effort
            discovered[model_id] = CodexModelOption(
                model_id=model_id,
                label=str(raw_model.get("display_name") or model_id),
                reasoning_efforts=efforts or DEFAULT_REASONING_EFFORTS,
                supports_fast_mode="fast" in speed_tiers,
                default_reasoning_effort=(
                    default_effort
                    if isinstance(default_effort, str)
                    and default_effort in SUPPORTED_REASONING_EFFORTS
                    else None
                ),
                is_default=is_default,
                default_fast_mode=configured_fast_mode,
            )
    except (OSError, ValueError, TypeError):
        pass

    if configured_model and configured_model not in discovered:
        discovered[configured_model] = CodexModelOption(
            configured_model, configured_model, DEFAULT_REASONING_EFFORTS,
            default_reasoning_effort=configured_effort, is_default=True,
            default_fast_mode=configured_fast_mode,
        )

    if configured:
        options = [
            discovered.get(
                model_id,
                CodexModelOption(
                    model_id,
                    model_id,
                    DEFAULT_REASONING_EFFORTS,
                    default_reasoning_effort=(
                        configured_effort if model_id == configured_model else None
                    ),
                    is_default=model_id == configured_model,
                    default_fast_mode=configured_fast_mode,
                ),
            )
            for model_id in configured
        ]
        if not any(option.is_default for option in options):
            options[0] = replace(options[0], is_default=True)
        return options
    return list(discovered.values())


def fetch_claude_model_catalog() -> list[dict[str, Any]] | None:
    """Read the signed-in Claude account's models from the SDK initialize response."""
    try:
        from .claude_agent_backend import ClaudeAgentRun
    except ImportError:
        from claude_agent_backend import ClaudeAgentRun
    try:
        return ClaudeAgentRun(cwd=default_workdir(), timeout=30).model_catalog()
    except Exception:  # noqa: BLE001 - discovery falls back to the CLI default model
        return None


def discover_claude_models() -> list[ModelOption]:
    """Offer the Claude account's live catalog; the CLI resolves its own default."""
    configured = [
        value.strip()
        for value in os.getenv("OPENTAG_CLAUDE_MODELS", "").split(",")
        if value.strip()
    ]
    discovered: dict[str, ModelOption] = {}
    for item in fetch_claude_model_catalog() or []:
        model_id = item.get("model")
        if not isinstance(model_id, str) or not model_id:
            continue
        efforts = tuple(
            effort for effort in item.get("supportedEfforts", [])
            if effort in SUPPORTED_REASONING_EFFORTS
        )
        discovered[model_id] = ModelOption(
            model_id=model_id,
            label=str(item.get("displayName") or model_id),
            reasoning_efforts=efforts,
            supports_fast_mode=item.get("supportsFastMode") is True,
            default_reasoning_effort=CLAUDE_DEFAULT_EFFORT if CLAUDE_DEFAULT_EFFORT in efforts else None,
            is_default=item.get("isDefault") is True,
            backend="claude",
            resolved_model=item.get("resolvedModel") if isinstance(item.get("resolvedModel"), str) else None,
        )
    if configured:
        options = [
            discovered.get(model_id, ModelOption(model_id, model_id, (), backend="claude"))
            for model_id in configured
        ]
        if not any(option.is_default for option in options):
            options[0] = replace(options[0], is_default=True)
        return options
    if not discovered:
        return [ModelOption("default", "Claude default", (), is_default=True, backend="claude")]
    return list(discovered.values())


def discover_models(backend: str) -> list[ModelOption]:
    if agent_connection.active(backend):
        agent_connection.validate(backend)
        return [ModelOption(name, name, (), is_default=index == 0, backend=backend)
                for index, name in enumerate(agent_connection.models(backend))]
    if backend == "codex":
        return discover_codex_models()
    if backend == "claude":
        return discover_claude_models()
    return []


def backend_signed_in(backend: str) -> bool:
    """Offer an additional backend only when it can run on this machine."""
    executable = shutil.which(backend)
    if not executable:
        return False
    if agent_connection.active(backend):
        try:
            agent_connection.validate(backend)
        except ValueError:
            return False
        if backend == "claude":
            import importlib.util
            return importlib.util.find_spec("claude_agent_sdk") is not None
        return True
    if backend == "codex":
        try:
            from . import tag_chatgpt
        except ImportError:
            import tag_chatgpt
        try:
            store = tag_chatgpt.Store()
            if store.enabled():
                account = store.status()["active_account"]
                return bool(account and account["signed_in"] and account["plan_enabled"]
                            and not account["usage_paused"])
        except (tag_chatgpt.ChatGPTError, OSError):
            return False
    if backend == "claude" and rich_events_selected("claude"):
        import importlib.util

        if importlib.util.find_spec("claude_agent_sdk") is None:
            return False
    command = [executable, "auth", "status"] if backend == "claude" else [executable, "login", "status"]
    try:
        return subprocess.run(command, capture_output=True, timeout=15, stdin=subprocess.DEVNULL,
                              check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def allowed_backends(default_backend: str) -> list[str]:
    """List the Tag's backends, default first; OPENTAG_BACKENDS may restrict the rest."""
    configured = [
        value.strip() for value in os.getenv("OPENTAG_BACKENDS", "").split(",")
        if value.strip() in BACKEND_NAMES
    ]
    others = [name for name in (configured or BACKEND_NAMES) if name != default_backend]
    return [default_backend, *others]


def tag_default_choice(default_backend: str) -> tuple[str, str | None]:
    raw = os.getenv("OPENTAG_DEFAULT_MODEL", "").strip()
    return parse_model_choice(raw, default_backend) if raw else (default_backend, None)


def discover_tag_models(default_backend: str) -> list[ModelOption]:
    """Combine every usable backend's catalog and mark the Tag's default model."""
    default_choice_backend, default_model = tag_default_choice(default_backend)
    models: list[ModelOption] = []
    connected = [name for name in allowed_backends(default_backend) if backend_signed_in(name)]
    if default_choice_backend not in connected and connected:
        default_choice_backend, default_model = connected[0], None
    for name in connected:
        options = discover_models(name)
        if name != default_choice_backend:
            options = [replace(item, is_default=False) for item in options]
        elif default_model:
            if not any(item.model_id == default_model for item in options):
                if agent_connection.active(name):
                    default_model = options[0].model_id if options else None
                else:
                    efforts = () if name == "claude" else DEFAULT_REASONING_EFFORTS
                    options.append(ModelOption(default_model, default_model, efforts, backend=name))
            options = [replace(item, is_default=item.model_id == default_model) for item in options]
        models.extend(options)
    return models


def model_names_path(home: Path | str) -> Path:
    return Path(home) / "state" / "model-names.json"


def remember_model_names(models: list[ModelOption], path: Path) -> None:
    """Save display names from a live catalog so status screens need no backend."""
    names = {item.value: item.label for item in models if item.label and item.label != item.model_id}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(names, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass  # Names are cosmetic; status falls back to model IDs.


def load_model_names(path: Path) -> dict[str, str]:
    try:
        names = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {key: value for key, value in names.items()
            if isinstance(key, str) and isinstance(value, str)} if isinstance(names, dict) else {}


def describe_model_choice(value: str, default_backend: str, *, names: dict[str, str] | None = None) -> str:
    """Render a saved choice without starting a backend, e.g. ``Claude · Opus 5.5``."""
    backend, model = parse_model_choice(value, default_backend) if value else (default_backend, None)
    label = (names or {}).get(f"{backend}:{model}", model) if model else "account default"
    return f"{backend_display_name(backend)} · {label}"


def default_model_choices(models: list[ModelOption]) -> list[tuple[str, str]]:
    """Return ``(value, label)`` choices for a Tag default, grouped by backend."""
    choices: list[tuple[str, str]] = []
    for backend in dict.fromkeys(item.backend for item in models):
        name = backend_display_name(backend)
        options = [item for item in models if item.backend == backend]
        if not any(item.model_id == "default" for item in options):
            choices.append((backend, f"{name} · account default"))
        choices.extend((item.value, f"{name} · {item.label}") for item in options)
    return choices
