#!/usr/bin/env python3
"""Warm MFS's default local embedding model cache during Tag installation."""
from __future__ import annotations



def _embedding_components():
    """Load MFS only when the installer executes this helper."""
    from mfs_server.common.embeddings import get_provider
    from mfs_server.config import EmbeddingConfig

    return EmbeddingConfig, get_provider


def preload_default_embedding() -> tuple[str, str]:
    """Download and validate the embedding model used by an unconfigured MFS server."""
    EmbeddingConfig, get_provider = _embedding_components()
    config = EmbeddingConfig()
    provider = get_provider(config.provider, config.model)
    return config.provider, provider.model_name


def main() -> int:
    provider, model = preload_default_embedding()
    print(f"MFS embedding model ready: {provider}/{model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
