"""Explicit per-Tag API connections, separate from inherited CLI sign-in."""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from urllib.parse import urlsplit


def mode(backend: str, values: Mapping[str, str] | None = None) -> str:
    return (os.environ if values is None else values).get(f"OPENTAG_{(backend or '').upper()}_AUTH", "inherit") or "inherit"


def active(backend: str, values: Mapping[str, str] | None = None) -> bool:
    return mode(backend, values) != "inherit"


def validate(backend: str, values: Mapping[str, str] | None = None) -> None:
    values = os.environ if values is None else values
    prefix = f"OPENTAG_{backend.upper()}_"
    selected = mode(backend, values)
    routing(backend, values)
    if selected not in ({"inherit", "api", "azure"} if backend == "codex" else {"inherit", "api"}):
        raise ValueError(f"Invalid {prefix}AUTH")
    if selected == "inherit":
        return
    if not values.get(prefix + "API_KEY", "").strip():
        raise ValueError(f"Set the API key in tag settings → API connections, or run tag config set {prefix}API_KEY --stdin")
    expected = "app-server" if backend == "codex" else "sdk"
    if values.get(prefix + "TRANSPORT", expected) != expected:
        raise ValueError(f"API connections require {prefix}TRANSPORT={expected}")
    url = values.get(prefix + "BASE_URL", "")
    if selected == "azure" and not url:
        raise ValueError("Set OPENTAG_CODEX_BASE_URL to your Azure Responses endpoint base URL")
    if url:
        validate_url(url)
    if not models(backend, values):
        raise ValueError(f"Set {prefix}MODELS to the provider's model or Azure deployment names")


def validate_url(value: str) -> None:
    try:
        url = urlsplit(value)
        _ = url.port
        valid = (not any(c.isspace() for c in value) and url.scheme in {"https", "http"} and url.hostname
                 and not (url.username or url.password or url.query or url.fragment)
                 and (url.scheme == "https" or url.hostname in {"localhost", "127.0.0.1", "::1"}))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Use an HTTPS base URL without credentials, query, or fragment (HTTP is allowed for localhost)")


def models(backend: str, values: Mapping[str, str] | None = None) -> list[str]:
    values = os.environ if values is None else values
    return list(dict.fromkeys(item.strip() for item in values.get(f"OPENTAG_{backend.upper()}_MODELS", "").split(",") if item.strip()))


def routing(backend: str, values: Mapping[str, str] | None = None) -> str | None:
    values = os.environ if values is None else values
    prefix = f"OPENTAG_{backend.upper()}_GATEWAY_"
    format_name = values.get(prefix + "FORMAT", "").strip()
    provider = values.get(prefix + "PROVIDER", "").strip()
    disable_tools = values.get(prefix + "DISABLE_TOOLS", "0") or "0"
    if disable_tools not in {"0", "1"}:
        raise ValueError("Gateway DISABLE_TOOLS must be 0 or 1")
    if disable_tools == "1":
        if backend != "codex":
            raise ValueError("Gateway chat-only mode is supported only for Codex, not Claude")
        if not format_name or not provider:
            raise ValueError("Gateway chat-only mode requires explicit gateway provider routing")
    if not format_name and not provider:
        return None
    if backend != "codex":
        raise ValueError("Gateway provider routing is supported only for Codex Responses connections, not Claude")
    if mode(backend, values) != "api":
        raise ValueError("Gateway provider routing requires OPENTAG_CODEX_AUTH=api")
    if format_name != "provider.only" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", provider):
        raise ValueError("Set gateway format to provider.only and specify one provider ID, or clear both routing settings")
    if not values.get("OPENTAG_CODEX_BASE_URL", ""):
        raise ValueError("Gateway provider routing requires an explicit OPENTAG_CODEX_BASE_URL")
    return provider


def codex_options(*, proxy_url: str | None = None) -> list[str]:
    routing("codex")
    if not active("codex"):
        return []
    validate("codex")
    azure = mode("codex") == "azure"
    provider: dict[str, object] = {
        "name": "Tag Azure" if azure else "Tag API",
        "base_url": os.getenv("OPENTAG_CODEX_BASE_URL") or "https://api.openai.com/v1",
        "wire_api": "responses", "requires_openai_auth": False,
        "supports_websockets": False,
    }
    # Azure resource keys are sent as api-key, never embedded in argv or a URL.
    if azure:
        provider["env_http_headers"] = {"api-key": "OPENTAG_CODEX_API_KEY"}
        version = os.getenv("OPENTAG_CODEX_API_VERSION", "")
        if version:
            provider["query_params"] = {"api-version": version}
    else:
        provider["env_key"] = "OPENTAG_CODEX_API_KEY"
    options = ["-c", 'model_provider="tag_api"', "-c", f"model={json.dumps(models('codex')[0])}",
               "-c", 'service_tier="default"']
    # Codex can advertise hosted web search even when the prompt never asks
    # for it. Gateways accepting only function/custom tools reject that entire
    # Responses request. Apply this on every launch, including existing Tags,
    # without rewriting the operator's global Codex configuration.
    if azure or urlsplit(str(provider["base_url"])).hostname != "api.openai.com":
        options += ["-c", 'web_search="disabled"',
                    "-c", "features.image_generation=false",
                    # Recent Codex exposes multi-agent tools as a Responses
                    # namespace, which function/custom-only gateways reject.
                    "-c", "agents.enabled=false", "-c", "features.multi_agent=false",
                    # MCP/app tools are also serialized as namespaces. Code
                    # Mode exposes them through exec (custom) and wait
                    # (function), retaining MCP tools without wire namespaces.
                    "-c", "features.code_mode=true", "-c", "features.code_mode_only=true"]
    def toml(value: object) -> str:
        if isinstance(value, dict):
            return "{" + ", ".join(f"{json.dumps(k)} = {toml(v)}" for k, v in value.items()) + "}"
        return json.dumps(value)
    options += ["-c", "model_providers.tag_api=" + toml(provider)]
    if proxy_url:
        options += ["-c", "model_providers.tag_api.base_url=" + json.dumps(proxy_url),
                    "-c", 'model_providers.tag_api.env_key="TAG_GATEWAY_PROXY_TOKEN"',
                    "-c", "features.enable_request_compression=false"]
    return options


def claude_environment() -> dict[str, str]:
    routing("claude")
    if not active("claude"):
        return {}
    validate("claude")
    # Empty overrides prevent inherited alternative auth/provider paths winning.
    return {"ANTHROPIC_API_KEY": os.environ["OPENTAG_CLAUDE_API_KEY"],
            "ANTHROPIC_BASE_URL": os.getenv("OPENTAG_CLAUDE_BASE_URL") or "https://api.anthropic.com",
            "ANTHROPIC_AUTH_TOKEN": "", "CLAUDE_CODE_OAUTH_TOKEN": "",
            "CLAUDE_CODE_USE_BEDROCK": "0", "CLAUDE_CODE_USE_VERTEX": "0",
            "CLAUDE_CODE_USE_FOUNDRY": "0"}
