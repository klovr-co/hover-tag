"""Choose how memory computes search embeddings: the built-in default, a hosted provider, or any OpenAI-compatible endpoint.

Tag writes its own copy of the MFS server configuration under the shared memory
folder, so a person's `~/.mfs/server.toml` is never rewritten. Tag's own choice (provider, model, base URL) is recorded next to it. A key
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
CHOICE_NAME = "embedding.json"
REINDEX_MARK = "embedding-reindex-pending"
LOCAL = "onnx"
CUSTOM = "custom"
PLACEHOLDER_KEY = "not-needed"  # the OpenAI client insists on some key, even for a local endpoint


@dataclass(frozen=True)
class Model:
    id: str
    dim: int


@dataclass(frozen=True)
class Provider:
    """A hosted embedding provider Tag can configure without installing anything extra."""
    id: str
    label: str
    detail: str
    models: tuple[Model, ...]
    env: str  # the variable MFS reads the key from
    url: str  # where the key is checked
    server_provider: str  # MFS's own name for it


# Only providers whose MFS client needs no extra SDK. Gemini, Voyage and Mistral would add
# packages to every install, so they stay a manual server.toml choice. Ollama, LM Studio and
# vLLM work through the custom OpenAI-compatible endpoint.
PROVIDERS = {p.id: p for p in (
    Provider("openai", "OpenAI", "Fast and cheap. Needs an OpenAI key.",
             (Model("text-embedding-3-small", 1536), Model("text-embedding-3-large", 3072)),
             "OPENAI_API_KEY", "https://api.openai.com/v1", "openai"),
    Provider("jina", "Jina", "Strong multilingual search. Needs a Jina key.",
             (Model("jina-embeddings-v4", 2048), Model("jina-embeddings-v3", 1024)),
             "JINA_API_KEY", "https://api.jina.ai/v1", "jina"),
    Provider(CUSTOM, "Custom", "Any OpenAI-compatible endpoint: Ollama, LM Studio, vLLM, or your own gateway.",
             (), "OPENAI_API_KEY", "", "openai"),
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


def choice(shared: Path) -> dict[str, object]:
    """What Tag last configured: provider, model, base URL (custom), vector size. Empty: the default."""
    try:
        data = json.loads((shared / CHOICE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and data.get("provider") in PROVIDERS else {}


def current_provider(shared: Path) -> str:
    """The provider Tag's own configuration selects; the built-in default when it selects none."""
    return str(choice(shared).get("provider") or LOCAL)


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


def endpoint(provider: str, base_url: str = "") -> str:
    """The base URL an OpenAI-style embeddings request goes to."""
    return (base_url or PROVIDERS[provider].url).rstrip("/")


def probe(provider: str, key: str, model: str, base_url: str = "") -> int:
    """Embed one word with this provider and return the vector size.

    Raises ValueError with a sentence a person can act on, and also proves the key, the
    model name and the address all work together before anything is saved.
    """
    chosen = PROVIDERS[provider]
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(
        endpoint(provider, base_url) + "/embeddings",
        data=json.dumps({"model": model, "input": "tag"}).encode(), headers=headers)
    host = urllib.parse.urlparse(endpoint(provider, base_url)).hostname or "the endpoint"
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            vector = json.load(response)["data"][0]["embedding"]
            if not isinstance(vector, list) or not vector:
                raise ValueError("empty")
            return len(vector)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise ValueError(f"{chosen.label} rejected that key.") from None
        if error.code == 404:
            raise ValueError(f"{host} has no embedding model called {model}, or no /embeddings endpoint.") from None
        if error.code == 429:
            raise ValueError(f"{chosen.label} says this key is out of quota or rate limited.") from None
        raise ValueError(f"{chosen.label} couldn't check the key (HTTP {error.code}).") from None
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError(f"{host} didn't answer like an OpenAI-compatible embeddings endpoint.") from None
    except OSError:
        raise ValueError(f"Couldn't reach {host} to check the key.") from None


def set_hosted(shared: Path, provider: str, key: str, *, model: str = "", base_url: str = "",
               verify: Callable[[str, str, str, str], int | None] | None = None) -> None:
    """Use a hosted or custom provider. Nothing is saved unless `verify` accepts it."""
    if provider not in PROVIDERS:
        raise ValueError(f"Choose one of: {', '.join(['local', *PROVIDERS])}")
    chosen, key, model, base_url = PROVIDERS[provider], key.strip(), model.strip(), base_url.strip()
    if any(char.isspace() for char in key):
        raise ValueError("That key has spaces in it.")
    if provider == CUSTOM:
        if not base_url.startswith(("http://", "https://")) or " " in base_url:
            raise ValueError("Enter the endpoint's address, starting with http:// or https://.")
        if not model:
            raise ValueError("Enter the embedding model's name.")
    else:
        base_url = ""
        model = model or chosen.models[0].id
        if model not in {m.id for m in chosen.models}:
            raise ValueError(f"{chosen.label} offers: {', '.join(m.id for m in chosen.models)}.")
        if not key:
            raise ValueError(f"Enter your {chosen.label} API key.")
    measured = (verify or (lambda *_: None))(provider, key, model, base_url)
    dim = measured or next((m.dim for m in chosen.models if m.id == model), 0)
    if not dim:
        raise ValueError("Couldn't tell the model's vector size; check the endpoint first.")
    lines = _without_embedding(_base_text(shared))
    if key:
        _write(key_path(shared, provider), key)
    else:
        key_path(shared, provider).unlink(missing_ok=True)
    _save_config(shared, lines, {"provider": chosen.server_provider, "model": model, "dim": dim})
    _write(shared / CHOICE_NAME, json.dumps({"provider": provider, "model": model, "dim": dim,
                                             **({"base_url": base_url} if base_url else {})}) + "\n")


def set_local(shared: Path) -> None:
    """Back to the built-in default model. Other server settings and saved keys stay."""
    lines = _without_embedding(_base_text(shared))
    if managed_config(shared) or lines:
        _save_config(shared, lines, None)
    (shared / CHOICE_NAME).unlink(missing_ok=True)


def problem(shared: Path) -> str | None:
    provider = current_provider(shared)
    if provider in PROVIDERS and provider != CUSTOM and not saved_key(shared, provider):
        return f"The {PROVIDERS[provider].label} key is missing. Save it again."
    return None


def server_environment(shared: Path) -> dict[str, str]:
    """What MFS needs in its environment to use the chosen provider."""
    path, chosen = managed_config(shared), choice(shared)
    if not path:
        return {}
    environment = {"MFS_SERVER_CONFIG": str(path)}
    provider = str(chosen.get("provider") or "")
    if provider in PROVIDERS:
        key = saved_key(shared, provider)
        if PROVIDERS[provider].server_provider == "openai":
            # Explicit, so a stray OPENAI_BASE_URL in someone's shell can't redirect memory.
            environment["OPENAI_BASE_URL"] = endpoint(provider, str(chosen.get("base_url") or ""))
            environment["OPENAI_API_KEY"] = key or PLACEHOLDER_KEY
        elif key:
            environment[PROVIDERS[provider].env] = key
    return environment


def choices(shared: Path) -> list[dict[str, object]]:
    """What a person can pick, for any interface to list: the built-in default first."""
    current = choice(shared)
    return [
        {"id": LOCAL, "label": "Default", "detail": "Built in. Runs on this computer, private, works offline. About 560 MB.",
         "hosted": False, "key_saved": False, "key_optional": False, "models": [], "needs_url": False,
         "model": "", "base_url": ""},
        *({"id": p.id, "label": p.label, "detail": p.detail, "hosted": True,
           "key_saved": saved_key(shared, p.id) is not None, "key_optional": p.id == CUSTOM,
           "models": [{"id": m.id, "dim": m.dim} for m in p.models], "needs_url": p.id == CUSTOM,
           "model": str(current.get("model") or "") if current.get("provider") == p.id else "",
           "base_url": str(current.get("base_url") or "") if current.get("provider") == p.id else ""}
          for p in PROVIDERS.values()),
    ]


def mark_reindex(shared: Path) -> None:
    """Sources must be indexed again into the new provider's collection; survives a restart."""
    _write(shared / REINDEX_MARK, "pending\n")


def reindex_pending(shared: Path) -> bool:
    return (shared / REINDEX_MARK).is_file()


def clear_reindex(shared: Path) -> None:
    (shared / REINDEX_MARK).unlink(missing_ok=True)
