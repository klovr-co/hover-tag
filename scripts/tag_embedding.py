"""Choose how memory computes search embeddings: the built-in Default, or one custom endpoint.

A custom endpoint is any OpenAI-compatible `/embeddings` address: OpenAI itself,
Ollama, LM Studio, vLLM, or a gateway. Tag writes its own copy of the MFS server
configuration under the shared memory folder, so a person's `~/.mfs/server.toml`
is never rewritten, and records its own choice (model, address, vector size)
beside it. A key lives in a private file there and reaches MFS through its
environment, never through argv or the configuration file. MFS keeps one
collection per vector size, so switching leaves the other index untouched.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

CONFIG_NAME = "server.toml"
CHOICE_NAME = "embedding.json"
KEY_NAME = "credentials/embedding-key"
REINDEX_MARK = "embedding-reindex-pending"
LOCAL = "onnx"
CUSTOM = "custom"
PLACEHOLDER_KEY = "not-needed"  # the OpenAI client insists on some key, even for a local endpoint
_TABLE = re.compile(r"^\s*\[+[^\]]*\]+\s*(#.*)?$")
_EMBEDDING = re.compile(r"^\s*\[embedding\]\s*(#.*)?$")


def managed_config(shared: Path) -> Path | None:
    path = shared / CONFIG_NAME
    return path if path.is_file() else None


def key_path(shared: Path) -> Path:
    return shared / KEY_NAME


def saved_key(shared: Path) -> str | None:
    try:
        return key_path(shared).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def choice(shared: Path) -> dict[str, object]:
    """What Tag last configured: model, base URL and vector size. Empty: the Default."""
    try:
        data = json.loads((shared / CHOICE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and data.get("provider") == CUSTOM else {}


def current_provider(shared: Path) -> str:
    return CUSTOM if choice(shared) else LOCAL


def _user_config() -> Path | None:
    """The server configuration MFS would otherwise use, in its own lookup order."""
    home = os.environ.get("MFS_HOME")
    for candidate in (os.environ.get("MFS_SERVER_CONFIG"), f"{home}/{CONFIG_NAME}" if home else None,
                      str(Path.home() / ".mfs" / CONFIG_NAME)):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def _without_embedding(text: str) -> list[str]:
    kept, skipping = [], False
    for line in text.splitlines():
        if _EMBEDDING.match(line):
            skipping = True
            continue
        if skipping and _TABLE.match(line):
            skipping = False
        if not skipping:
            kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    return kept


def _write(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".tag-embedding-", dir=path.parent)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _base_text(shared: Path) -> str:
    source = managed_config(shared) or _user_config()
    return source.read_text(encoding="utf-8") if source else ""


def _save_config(shared: Path, lines: list[str], embedding: dict | None) -> None:
    if embedding:
        lines = [*lines, *([""] if lines else []), "[embedding]",
                 *(f'{k} = {json.dumps(v)}' for k, v in embedding.items())]
    _write(shared / CONFIG_NAME, "\n".join(lines) + "\n")


def probe(key: str, model: str, base_url: str) -> int:
    """Embed one word at this address and return the vector size.

    Raises ValueError with a sentence a person can act on, and also proves the key, the
    model name and the address work together before anything is saved.
    """
    base = base_url.rstrip("/")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(base + "/embeddings", headers=headers,
                                     data=json.dumps({"model": model, "input": "tag"}).encode())
    host = urllib.parse.urlparse(base).hostname or "the endpoint"
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            vector = json.load(response)["data"][0]["embedding"]
            if not isinstance(vector, list) or not vector:
                raise ValueError("empty")
            return len(vector)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise ValueError(f"{host} rejected that key.") from None
        if error.code == 404:
            raise ValueError(f"{host} has no embedding model called {model}, or no /embeddings endpoint.") from None
        if error.code == 429:
            raise ValueError(f"{host} says this key is out of quota or rate limited.") from None
        raise ValueError(f"{host} couldn't check the key (HTTP {error.code}).") from None
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError(f"{host} didn't answer like an OpenAI-compatible embeddings endpoint.") from None
    except OSError:
        raise ValueError(f"Couldn't reach {host} to check the key.") from None


def set_custom(shared: Path, key: str, *, model: str, base_url: str,
               verify: Callable[[str, str, str], int | None] | None = None) -> None:
    """Use a custom endpoint. Nothing is saved unless `verify` accepts it; it also measures the vector size."""
    key, model, base_url = key.strip(), model.strip(), base_url.strip()
    if any(char.isspace() for char in key):
        raise ValueError("That key has spaces in it.")
    if not base_url.startswith(("http://", "https://")) or any(char.isspace() for char in base_url):
        raise ValueError("Enter the endpoint's address, starting with http:// or https://.")
    if not model:
        raise ValueError("Enter the embedding model's name.")
    dim = (verify or (lambda *_: None))(key, model, base_url)
    if not dim:
        raise ValueError("Couldn't tell the model's vector size; check the endpoint first.")
    lines = _without_embedding(_base_text(shared))
    if key:
        _write(key_path(shared), key)
    else:
        key_path(shared).unlink(missing_ok=True)
    _save_config(shared, lines, {"provider": "openai", "model": model, "dim": dim})
    _write(shared / CHOICE_NAME, json.dumps({"provider": CUSTOM, "model": model, "dim": dim, "base_url": base_url}) + "\n")


def set_local(shared: Path) -> None:
    """Back to the built-in Default. Other server settings and the saved key stay."""
    lines = _without_embedding(_base_text(shared))
    if managed_config(shared) or lines:
        _save_config(shared, lines, None)
    (shared / CHOICE_NAME).unlink(missing_ok=True)


def server_environment(shared: Path) -> dict[str, str]:
    """What MFS needs in its environment to use the chosen endpoint."""
    path, chosen = managed_config(shared), choice(shared)
    if not path:
        return {}
    environment = {"MFS_SERVER_CONFIG": str(path)}
    if chosen:
        # Set explicitly, so a stray OPENAI_BASE_URL in someone's shell can't redirect memory.
        environment["OPENAI_BASE_URL"] = str(chosen["base_url"]).rstrip("/")
        environment["OPENAI_API_KEY"] = saved_key(shared) or PLACEHOLDER_KEY
    return environment


def choices(shared: Path) -> list[dict[str, object]]:
    """What a person can pick, for any interface to list: the Default first."""
    current = choice(shared)
    return [
        {"id": LOCAL, "label": "Default", "detail": "Built in. Runs on this computer, private, works offline. About 560 MB.",
         "custom": False, "model": "", "base_url": "", "key_saved": False},
        {"id": CUSTOM, "label": "Custom",
         "detail": "Any OpenAI-compatible endpoint: OpenAI, Ollama, LM Studio, vLLM, or your own gateway.",
         "custom": True, "model": str(current.get("model") or ""), "base_url": str(current.get("base_url") or ""),
         "key_saved": saved_key(shared) is not None},
    ]


def mark_reindex(shared: Path) -> None:
    """Sources must be indexed again into the new provider's collection; survives a restart."""
    _write(shared / REINDEX_MARK, "pending\n")


def reindex_pending(shared: Path) -> bool:
    return (shared / REINDEX_MARK).is_file()


def clear_reindex(shared: Path) -> None:
    (shared / REINDEX_MARK).unlink(missing_ok=True)
