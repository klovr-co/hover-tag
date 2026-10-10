"""Choose where memory computes search embeddings: on this computer, or a hosted provider.

Tag writes its own copy of the MFS server configuration under the shared memory
folder, so a person's `~/.mfs/server.toml` is never rewritten. The OpenAI key
lives in a private file there and reaches MFS through its environment, never
through argv or the configuration file. MFS keeps one collection per vector
size, so switching provider leaves the other index untouched.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

CONFIG_NAME = "server.toml"
REINDEX_MARK = "embedding-reindex-pending"
LOCAL = "onnx"


@dataclass(frozen=True)
class Provider:
    """A hosted embedding provider Tag can configure without installing anything extra."""
    id: str
    label: str
    model: str
    dim: int
    env: str  # the variable MFS reads the key from
    url: str  # where the key is checked
    detail: str


# Only providers whose MFS client needs no extra SDK. Others (Gemini, Voyage, Mistral,
# Ollama) would add packages to every install, so they stay a manual server.toml choice.
PROVIDERS = {p.id: p for p in (
    Provider("openai", "OpenAI", "text-embedding-3-small", 1536, "OPENAI_API_KEY",
             "https://api.openai.com/v1/embeddings", "Fast and cheap. Needs an OpenAI key."),
    Provider("jina", "Jina", "jina-embeddings-v4", 2048, "JINA_API_KEY",
             "https://api.jina.ai/v1/embeddings", "Strong multilingual search. Needs a Jina key."),
)}
_TABLE = re.compile(r"^\s*\[+[^\]]*\]+\s*(#.*)?$")
_EMBEDDING = re.compile(r"^\s*\[embedding\]\s*(#.*)?$")


def managed_config(shared: Path) -> Path | None:
    path = shared / CONFIG_NAME
    return path if path.is_file() else None


def key_path(shared: Path, provider: str) -> Path:
    return shared / f"credentials/{provider}-embedding-key"


def saved_key(shared: Path, provider: str) -> str | None:
    try:
        return key_path(shared, provider).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def current_provider(shared: Path) -> str:
    """The provider Tag's own configuration selects; the local model when it selects none."""
    path = managed_config(shared)
    if path:
        match = re.search(r'^\s*provider\s*=\s*"([^"]+)"', "\n".join(_embedding_lines(path.read_text(encoding="utf-8"))), re.M)
        if match:
            return match.group(1)
    return LOCAL


def _user_config() -> Path | None:
    """The server configuration MFS would otherwise use, in its own lookup order."""
    home = os.environ.get("MFS_HOME")
    for candidate in (os.environ.get("MFS_SERVER_CONFIG"), f"{home}/{CONFIG_NAME}" if home else None,
                      str(Path.home() / ".mfs" / CONFIG_NAME)):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def _embedding_lines(text: str) -> list[str]:
    found, inside = [], False
    for line in text.splitlines():
        if _EMBEDDING.match(line):
            inside = True
        elif inside and _TABLE.match(line):
            inside = False
        elif inside:
            found.append(line)
    return found


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


def check_key(provider: str, key: str) -> None:
    """Ask the provider for one tiny embedding. Raises ValueError with a sentence a person can act on."""
    chosen = PROVIDERS[provider]
    request = urllib.request.Request(
        chosen.url, data=json.dumps({"model": chosen.model, "input": "tag"}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20):
            return
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise ValueError(f"{chosen.label} rejected that key.") from None
        if error.code == 429:
            raise ValueError(f"{chosen.label} says this key is out of quota or rate limited.") from None
        raise ValueError(f"{chosen.label} couldn't check the key (HTTP {error.code}).") from None
    except OSError:
        raise ValueError(f"Couldn't reach {urllib.parse.urlparse(chosen.url).hostname} to check the key.") from None


def set_hosted(shared: Path, provider: str, key: str, *, verify: Callable[[str, str], None] | None = None) -> None:
    """Use a hosted provider. Nothing is saved unless the key passes `verify`."""
    if provider not in PROVIDERS:
        raise ValueError(f"Choose one of: {', '.join(['local', *PROVIDERS])}")
    key = key.strip()
    if not key or any(char.isspace() for char in key):
        raise ValueError(f"Enter your {PROVIDERS[provider].label} API key.")
    (verify or (lambda _provider, _key: None))(provider, key)
    chosen = PROVIDERS[provider]
    lines = _without_embedding(_base_text(shared))
    _write(key_path(shared, provider), key)
    _save_config(shared, lines, {"provider": chosen.id, "model": chosen.model, "dim": chosen.dim})


def set_local(shared: Path) -> None:
    """Back to the default on-computer model. Other server settings and the saved key stay."""
    lines = _without_embedding(_base_text(shared))
    if managed_config(shared) or lines:
        _save_config(shared, lines, None)


def problem(shared: Path) -> str | None:
    provider = current_provider(shared)
    if provider in PROVIDERS and not saved_key(shared, provider):
        return f"The {PROVIDERS[provider].label} key is missing. Save it again."
    return None


def server_environment(shared: Path) -> dict[str, str]:
    """What MFS needs in its environment to use the chosen provider."""
    path = managed_config(shared)
    if not path:
        return {}
    environment = {"MFS_SERVER_CONFIG": str(path)}
    provider = current_provider(shared)
    key = saved_key(shared, provider) if provider in PROVIDERS else None
    if key:
        environment[PROVIDERS[provider].env] = key
    return environment


def choices(shared: Path) -> list[dict[str, object]]:
    """What a person can pick, for any interface to list: the local model first."""
    return [
        {"id": LOCAL, "label": "On this computer", "detail": "Private and works offline. About 560 MB.",
         "hosted": False, "key_saved": False},
        *({"id": p.id, "label": p.label, "detail": p.detail, "hosted": True,
           "key_saved": saved_key(shared, p.id) is not None} for p in PROVIDERS.values()),
    ]


def mark_reindex(shared: Path) -> None:
    """Sources must be indexed again into the new provider's collection; survives a restart."""
    _write(shared / REINDEX_MARK, "pending\n")


def reindex_pending(shared: Path) -> bool:
    return (shared / REINDEX_MARK).is_file()


def clear_reindex(shared: Path) -> None:
    (shared / REINDEX_MARK).unlink(missing_ok=True)
