"""Choose where memory computes search embeddings: on this computer, or OpenAI.

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
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

CONFIG_NAME = "server.toml"
REINDEX_MARK = "embedding-reindex-pending"
KEY_NAME = "credentials/openai-embedding-key"
OPENAI = {"provider": "openai", "model": "text-embedding-3-small", "dim": 1536}
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


def check_key(key: str) -> None:
    """Ask OpenAI for one tiny embedding. Raises ValueError with a sentence a person can act on."""
    request = urllib.request.Request(
        "https://api.openai.com/v1/embeddings",
        data=json.dumps({"model": OPENAI["model"], "input": "tag"}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20):
            return
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise ValueError("OpenAI rejected that key.") from None
        if error.code == 429:
            raise ValueError("OpenAI says this key is out of quota or rate limited.") from None
        raise ValueError(f"OpenAI couldn't check the key (HTTP {error.code}).") from None
    except OSError:
        raise ValueError("Couldn't reach api.openai.com to check the key.") from None


def set_openai(shared: Path, key: str, *, verify: Callable[[str], None] | None = None) -> None:
    """Use OpenAI embeddings. Nothing is saved unless the key passes `verify`."""
    key = key.strip()
    if not key or any(char.isspace() for char in key):
        raise ValueError("Enter your OpenAI API key.")
    (verify or (lambda _key: None))(key)
    lines = _without_embedding(_base_text(shared))
    _write(key_path(shared), key)
    _save_config(shared, lines, OPENAI)


def set_local(shared: Path) -> None:
    """Back to the default on-computer model. Other server settings and the saved key stay."""
    lines = _without_embedding(_base_text(shared))
    if managed_config(shared) or lines:
        _save_config(shared, lines, None)


def problem(shared: Path) -> str | None:
    if managed_config(shared) and 'provider = "openai"' in managed_config(shared).read_text(encoding="utf-8") \
            and not saved_key(shared):
        return "The OpenAI key is missing. Save it again."
    return None


def server_environment(shared: Path) -> dict[str, str]:
    """What MFS needs in its environment to use the chosen provider."""
    path = managed_config(shared)
    if not path:
        return {}
    environment = {"MFS_SERVER_CONFIG": str(path)}
    key = saved_key(shared)
    if key and 'provider = "openai"' in path.read_text(encoding="utf-8"):
        environment["OPENAI_API_KEY"] = key
    return environment


def mark_reindex(shared: Path) -> None:
    """Sources must be indexed again into the new provider's collection; survives a restart."""
    _write(shared / REINDEX_MARK, "pending\n")


def reindex_pending(shared: Path) -> bool:
    return (shared / REINDEX_MARK).is_file()


def clear_reindex(shared: Path) -> None:
    (shared / REINDEX_MARK).unlink(missing_ok=True)
