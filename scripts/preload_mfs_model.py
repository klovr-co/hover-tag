#!/usr/bin/env python3
"""Warm MFS's default local embedding model cache during Tag installation.

The model is a large download, so it is skipped when MFS will not use it:
either the server configuration selects a hosted embedding provider, or
TAG_SKIP_MFS_MODEL=1 says so (for installs that choose the provider later).
A skipped model is fetched by MFS itself the first time it starts with the
local provider.
"""
from __future__ import annotations

import os
from pathlib import Path

LOCAL_PROVIDER = "onnx"
SKIP_ENVIRONMENT = "TAG_SKIP_MFS_MODEL"


def _embedding_components():
    """Load MFS only when the installer executes this helper."""
    from mfs_server.common.embeddings import get_provider
    from mfs_server.config import EmbeddingConfig

    return EmbeddingConfig, get_provider


def _server_config_loader():
    from mfs_server.config import load_server_config

    return load_server_config


def configured_embedding(config_path=None) -> tuple[str, str]:
    """The provider and model the MFS server configuration selects.

    Reads the same server.toml lookup chain as the server. A configuration that
    cannot be read keeps the local default, which is what MFS falls back to.
    """
    try:
        embedding = _server_config_loader()(str(config_path) if config_path else None, apply_env=False).embedding
        return embedding.provider, embedding.model
    except Exception:
        return LOCAL_PROVIDER, ""


def uses_local_model(provider: str) -> bool:
    return provider == LOCAL_PROVIDER


def _managed_config():
    """The server configuration Tag keeps when someone picks a provider in Tag, if any."""
    home = os.environ.get("TAG_HOME")
    if not home:
        return None
    path = Path(home) / "shared/mfs/server.toml"
    return path if path.is_file() else None


def preload_default_embedding() -> tuple[str, str]:
    """Download and validate the embedding model used by an unconfigured MFS server."""
    EmbeddingConfig, get_provider = _embedding_components()
    config = EmbeddingConfig()
    provider = get_provider(config.provider, config.model)
    return config.provider, provider.model_name


def main() -> int:
    if os.environ.get(SKIP_ENVIRONMENT, "").strip().lower() in {"1", "true", "yes", "on"}:
        print(f"MFS embedding model download skipped ({SKIP_ENVIRONMENT} is set)")
        return 0
    configured, _ = configured_embedding(_managed_config())
    if not uses_local_model(configured):
        print(f"MFS embedding model download skipped (embedding provider is {configured})")
        return 0
    provider, model = preload_default_embedding()
    print(f"MFS embedding model ready: {provider}/{model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
