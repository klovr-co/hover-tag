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


SUPPORTED_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max", "ultra")
DEFAULT_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")
EFFORT_LABELS = {"minimal": "Minimal", "low": "Low", "medium": "Medium", "high": "High",
                 "xhigh": "Extra high", "max": "Max", "ultra": "Ultra"}


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
    # Set when the Tag's thinking level replaced the model's own default, which is kept here.
    tag_effort_applied: bool = False
    model_reasoning_effort: str | None = None

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


def tag_default_effort() -> str | None:
    value = os.getenv("OPENTAG_DEFAULT_EFFORT", "").strip()
    return value if value in SUPPORTED_REASONING_EFFORTS else None


def apply_tag_effort(models: list[ModelOption], effort: str | None) -> list[ModelOption]:
    """Use the Tag's thinking level as its default model's default, when the model offers it."""
    if not effort:
        return models
    return [
        replace(item, default_reasoning_effort=effort, tag_effort_applied=True,
                model_reasoning_effort=item.default_reasoning_effort)
        if item.is_default and effort in item.reasoning_efforts else item
        for item in models
    ]


def discover_tag_models(default_backend: str) -> list[ModelOption]:
    """Combine every usable backend's catalog and mark the Tag's default model.

    The Tag's thinking level (``OPENTAG_DEFAULT_EFFORT``) becomes the default
    model's default level, so every backend receives it; people's own choices
    in Slack still win.
    """
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
                efforts = () if name == "claude" else DEFAULT_REASONING_EFFORTS
                options.append(ModelOption(default_model, default_model, efforts, backend=name))
            options = [replace(item, is_default=item.model_id == default_model) for item in options]
        models.extend(options)
    return apply_tag_effort(models, tag_default_effort())


def model_names_path(home: Path | str) -> Path:
    return Path(home) / "state" / "model-names.json"


def model_efforts_path(home: Path | str) -> Path:
    return Path(home) / "state" / "model-efforts.json"


def _write_json(path: Path, value: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass  # Cosmetic; status falls back to model IDs and unknown thinking levels.


def remember_model_names(models: list[ModelOption], path: Path, *, account_defaults: bool = False) -> None:
    """Save display names and thinking levels from a live catalog so status screens need no backend.

    Levels go to ``model-efforts.json`` beside the names, keyed like the names.
    With ``account_defaults``, each backend's ``is_default`` model is the
    account's own default, so its levels are also saved under the bare
    backend (``codex``); otherwise earlier bare entries are kept.
    """
    names = {item.value: item.label for item in models if item.label and item.label != item.model_id}
    _write_json(path, names)
    efforts_path = path.with_name("model-efforts.json")
    efforts: dict[str, Any] = {} if account_defaults else {
        key: value for key, value in load_model_efforts(efforts_path).items() if key in BACKEND_NAMES
    }
    for item in models:
        default = item.model_reasoning_effort if item.tag_effort_applied else item.default_reasoning_effort
        entry = {"efforts": list(item.reasoning_efforts),
                 "default": default if default in item.reasoning_efforts else None}
        if item.model_id == "default" and not item.reasoning_efforts:
            continue  # Claude's placeholder for an unreported catalog says nothing about levels.
        efforts[item.value] = entry
        if account_defaults and item.is_default:
            efforts[item.backend] = entry
    _write_json(efforts_path, efforts)


def load_model_efforts(path: Path) -> dict[str, dict[str, Any]]:
    """Read saved thinking levels, ignoring anything malformed."""
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(saved, dict):
        return {}
    efforts: dict[str, dict[str, Any]] = {}
    for key, entry in saved.items():
        if not isinstance(key, str) or not isinstance(entry, dict) or not isinstance(entry.get("efforts"), list):
            continue
        levels = [level for level in entry["efforts"] if level in SUPPORTED_REASONING_EFFORTS]
        default = entry.get("default")
        efforts[key] = {"efforts": levels, "default": default if default in levels else None}
    return efforts


def choice_key(value: str, default_backend: str) -> str:
    backend, model = parse_model_choice(value, default_backend)
    return f"{backend}:{model}" if model else backend


def effort_levels(home: Path | str, value: str, default_backend: str = "codex") -> list[str]:
    """The thinking levels a model choice offers, from the saved catalog; [] when unknown or none."""
    entry = load_model_efforts(model_efforts_path(home)).get(choice_key(value, default_backend))
    return list(entry["efforts"]) if entry else []


def effective_effort(home: Path | str, values: dict[str, str]) -> str | None:
    """The thinking level the Tag's default model uses, without starting a backend.

    The Tag's own level when its default model offers it (or nothing is known
    about the model yet), otherwise the model's default; None for a model
    without thinking levels or when nothing is known.
    """
    backend = values.get("OPENTAG_BACKEND") or "codex"
    value = values.get("OPENTAG_DEFAULT_MODEL") or backend
    chosen = values.get("OPENTAG_DEFAULT_EFFORT", "")
    chosen = chosen if chosen in SUPPORTED_REASONING_EFFORTS else ""
    entry = load_model_efforts(model_efforts_path(home)).get(choice_key(value, backend))
    if entry is None:
        return chosen or None
    if chosen in entry["efforts"]:
        return chosen
    return entry["default"]


def effort_label(effort: str | None) -> str:
    return EFFORT_LABELS.get(effort or "", effort or "Model default")


def load_model_names(path: Path) -> dict[str, str]:
    try:
        names = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {key: value for key, value in names.items()
            if isinstance(key, str) and isinstance(value, str)} if isinstance(names, dict) else {}


def model_choice_name(value: str, default_backend: str, *, names: dict[str, str] | None = None) -> str:
    """Just the model's name for a saved choice, e.g. ``Opus 5.5`` or ``Account default``."""
    backend, model = parse_model_choice(value, default_backend) if value else (default_backend, None)
    return (names or {}).get(f"{backend}:{model}", model) if model else "Account default"


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
