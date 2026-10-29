"""Saved model catalogs for the shared AI accounts, so choosing a model starts no backend.

Reading Codex's catalog starts ``codex app-server`` (or asks ChatGPT), and
Claude's starts an Agent SDK session. Each account's catalog, exactly as the
backend reported it, is saved once per installation and reused by every Tag;
each Tag's own model settings still apply on top when it is read. A catalog is
keyed by a one-way digest of the account it came from, so another sign-in never
sees it. Nothing here stores credentials.

A saved catalog older than ``MAX_AGE_SECONDS`` is still offered at once, and
read again in the background for next time (stale-while-revalidate).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from . import agent_connection, agent_models
    from .opentag_process_env import without_telemetry_environment
    from .tag_locks import LifecycleLock
    from .tag_paths import tag_home
except ImportError:
    import agent_connection
    import agent_models
    from opentag_process_env import without_telemetry_environment
    from tag_locks import LifecycleLock
    from tag_paths import tag_home


# A saved catalog older than this is read again in the background after it is
# offered. Providers add models every few weeks; a day keeps the list current.
MAX_AGE_SECONDS: float | None = 24 * 60 * 60
FETCHERS = {"codex": "fetch_codex_model_catalog", "claude": "fetch_claude_model_catalog"}


def path(backend: str) -> Path:
    return tag_home() / "shared/ai/models" / f"{backend}.json"


def _read_json(file: Path) -> dict[str, Any]:
    try:
        value = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _digest(parts: list[Any]) -> str:
    return hashlib.sha256(json.dumps(parts).encode("utf-8")).hexdigest()[:24]


def account_key(backend: str) -> str | None:
    """Identify the account a backend's catalog comes from, from local files only.

    A ChatGPT plan is its selected registration; a Codex or Claude sign-in is
    the account its CLI saved. Without one, the backend and its sign-in method
    stand in. None when the account can't be read, so nothing is saved for it.
    """
    if backend == "codex":
        try:
            from . import tag_chatgpt
        except ImportError:
            import tag_chatgpt
        try:
            store = tag_chatgpt.Store()
            if store.enabled():
                return _digest(["codex", *store.identity()])
        except (tag_chatgpt.ChatGPTError, OSError, KeyError, TypeError):
            return None
        codex_home = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
        auth = _read_json(codex_home / "auth.json")
        tokens = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else {}
        key = auth.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        return _digest(["codex", "codex", tokens.get("account_id"), key])
    if backend == "claude":
        config_dir = Path(os.getenv("CLAUDE_CONFIG_DIR", str(Path.home()))).expanduser()
        account = _read_json(config_dir / ".claude.json").get("oauthAccount")
        account = account if isinstance(account, dict) else {}
        key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_CODE_OAUTH_TOKEN")
        return _digest(["claude", "claude", account.get("accountUuid"), account.get("organizationUuid"), key])
    return None


def stale(fetched_at: datetime, now: datetime | None = None) -> bool:
    """Whether a saved catalog should be read again after it is offered; see MAX_AGE_SECONDS."""
    if MAX_AGE_SECONDS is None:
        return False
    return ((now or datetime.now(timezone.utc)) - fetched_at).total_seconds() > MAX_AGE_SECONDS


def load(backend: str, account: str | None) -> tuple[list[dict[str, Any]], datetime] | None:
    """The saved catalog and when it was read, if it belongs to this account."""
    saved = _read_json(path(backend))
    if account is None or saved.get("schema_version") != 1 or saved.get("account") != account:
        return None
    catalog = saved.get("catalog")
    try:
        fetched_at = datetime.fromisoformat(saved["fetched_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if not isinstance(catalog, list) or not catalog or fetched_at.tzinfo is None:
        return None
    return [item for item in catalog if isinstance(item, dict)], fetched_at


def save(backend: str, account: str, catalog: list[dict[str, Any]], fetched_at: datetime) -> None:
    file = path(backend)
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{backend}-", dir=file.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"schema_version": 1, "backend": backend, "account": account,
                           "fetched_at": fetched_at.isoformat(timespec="seconds"), "catalog": catalog}, handle)
                handle.write("\n")
            os.replace(temporary, file)
        finally:
            Path(temporary).unlink(missing_ok=True)
    except OSError:
        pass  # Only speed is lost; the next read fetches again.


def forget(backend: str | None = None) -> None:
    """Drop saved catalogs after a sign-in change: another account's list is wrong, not just old."""
    for name in [backend] if backend else list(FETCHERS):
        try:
            path(name).unlink(missing_ok=True)
        except OSError:
            pass


class Source:
    """One backend's raw catalog for ``agent_models.discover_models``: saved, or read live and saved.

    ``live`` skips the saved catalog. After a call, ``fetched_at`` says when
    the catalog was read, ``cached`` whether it came from the saved file,
    ``stale`` whether that file is old enough to read again, and ``failed``
    whether a live read failed and the saved catalog was kept instead.
    """

    def __init__(self, backend: str, *, live: bool = False):
        self.backend, self.live = backend, live
        self.cached = self.stale = self.failed = False
        self.fetched_at: datetime | None = None

    def __call__(self) -> list[dict[str, Any]] | None:
        account = account_key(self.backend)
        if not self.live and (saved := load(self.backend, account)):
            catalog, self.fetched_at = saved
            self.cached, self.stale = True, stale(self.fetched_at)
            return catalog
        catalog = getattr(agent_models, FETCHERS[self.backend])()
        if not catalog and account is not None and (saved := load(self.backend, account)):
            # A failed read keeps the account's saved catalog rather than a fallback guess.
            catalog, self.fetched_at = saved
            self.cached = self.failed = True
            return catalog
        self.fetched_at = datetime.now(timezone.utc)
        # An empty or failed read is never saved, so the next read tries again.
        if catalog and account is not None:
            save(self.backend, account, catalog, self.fetched_at)
        return catalog


def cacheable(backend: str) -> bool:
    """A Tag's own API lists its models from settings; only shared accounts are saved."""
    return backend in FETCHERS and not agent_connection.active(backend)


def refresh(backends: list[str]) -> None:
    """Read these backends' catalogs live and save them; one refresh per backend at a time."""
    for backend in backends:
        if backend not in FETCHERS:
            continue
        lock = LifecycleLock(tag_home() / "state" / f"model-refresh-{backend}.lock")
        try:
            lock.acquire()
        except RuntimeError:
            continue  # Another process is already reading it.
        try:
            Source(backend, live=True)()
        finally:
            lock.release()


def refresh_in_background(backends: list[str]) -> None:
    """Start ``refresh`` in its own process, which outlives a short CLI call.

    It inherits the environment, so it reads from the same Tag workspace.
    """
    options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
               if os.name == "nt" else {"start_new_session": True})
    try:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "refresh", *backends],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env=without_telemetry_environment(os.environ), **options)
    except OSError:
        pass  # The saved catalog stays; the next read tries again.


if __name__ == "__main__" and sys.argv[1:2] == ["refresh"]:
    refresh(sys.argv[2:])
