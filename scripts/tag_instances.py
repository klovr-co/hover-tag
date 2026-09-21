"""Discovery and atomic creation of independent Tag instance homes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

try:
    from .tag_paths import initialize_instance
except ImportError:
    from tag_paths import initialize_instance


DEFAULT_TAG = "default"
SCHEMA_VERSION = 1
NAME_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?")


@dataclass(frozen=True)
class InstanceContext:
    installation_root: Path
    tag_id: str
    home: Path

    @property
    def is_default(self) -> bool:
        return self.tag_id == DEFAULT_TAG

    @property
    def shared_mfs_home(self) -> Path:
        return self.installation_root / "shared/mfs"

    def command(self, action: str) -> str:
        suffix = "" if self.is_default else f" --tag {self.tag_id}"
        return f"tag {action}{suffix}"


def validate_name(name: str, *, allow_default: bool = True) -> str:
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        raise ValueError(
            "Tag names must be 1-32 lowercase letters, digits, or hyphens"
        )
    if name == DEFAULT_TAG and not allow_default:
        raise ValueError("The name 'default' is reserved for the built-in Tag")
    return name


def instance_path(installation_root: Path, tag_id: str) -> Path:
    validate_name(tag_id)
    root = installation_root.expanduser().absolute()
    instances = root / "instances"
    candidate = instances / tag_id
    # Refuse a pre-existing symlink and ensure resolution cannot escape even if
    # an attacker races a parent replacement on a shared local account.
    if candidate.is_symlink():
        raise ValueError(f"Tag instance path must not be a symlink: {candidate}")
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(instances.resolve(strict=False)):
        raise ValueError("Tag instance path escapes the installation root")
    return candidate


def resolve(installation_root: Path, tag_id: str = DEFAULT_TAG, *, require_exists: bool = True) -> InstanceContext:
    home = instance_path(installation_root, tag_id)
    # The built-in default is addressable before first-run initialization so
    # read-only status/inspection can report "not configured" without writes.
    if require_exists and (tag_id != DEFAULT_TAG or home.exists()):
        metadata = home / "instance.json"
        if not home.is_dir() or home.is_symlink() or not metadata.is_file():
            raise ValueError(f"Unknown Tag '{tag_id}'. Run tag add {tag_id} first.")
        try:
            record = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            raise ValueError(f"Tag '{tag_id}' has malformed metadata") from None
        if record.get("schema_version") != SCHEMA_VERSION or record.get("id") != tag_id:
            raise ValueError(f"Tag '{tag_id}' has malformed metadata")
    return InstanceContext(installation_root.expanduser().absolute(), tag_id, home)


def ensure_default(installation_root: Path) -> InstanceContext:
    """Create the built-in default instance with the same layout as its peers."""
    root = installation_root.expanduser().absolute()
    home = instance_path(root, DEFAULT_TAG)
    if home.exists() or home.is_symlink():
        if not home.is_dir() or home.is_symlink():
            raise ValueError(f"Tag instance path must be a regular directory: {home}")
        initialize_instance(home)
        metadata = home / "instance.json"
        if not metadata.exists():
            _write_metadata(metadata, DEFAULT_TAG)
        return resolve(root, DEFAULT_TAG)
    return _create(root, DEFAULT_TAG)


def _write_metadata(path: Path, tag_id: str) -> None:
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "id": tag_id,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def create(installation_root: Path, tag_id: str) -> InstanceContext:
    validate_name(tag_id, allow_default=False)
    return _create(installation_root, tag_id)


def _create(installation_root: Path, tag_id: str) -> InstanceContext:
    validate_name(tag_id)
    root = installation_root.expanduser().absolute()
    instances = root / "instances"
    instances.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = instance_path(root, tag_id)
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"Tag '{tag_id}' already exists; its configuration was preserved")
    staging = Path(tempfile.mkdtemp(prefix=f".{tag_id}-", dir=instances))
    try:
        initialize_instance(staging)
        path = staging / "instance.json"
        _write_metadata(path, tag_id)
        try:
            os.rename(staging, destination)
        except FileExistsError:
            raise ValueError(f"Tag '{tag_id}' already exists; its configuration was preserved") from None
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return resolve(root, tag_id)


def discover(installation_root: Path) -> list[dict[str, object]]:
    """Return every visible instance without letting one bad entry hide peers."""
    root = installation_root.expanduser().absolute()
    result: list[dict[str, object]] = []
    directory = root / "instances"
    try:
        entries = sorted(
            directory.iterdir(),
            key=lambda item: (item.name != DEFAULT_TAG, item.name),
        )
    except OSError:
        entries = []
    if not any(entry.name == DEFAULT_TAG for entry in entries):
        result.append({
            "id": DEFAULT_TAG,
            "home": str(instance_path(root, DEFAULT_TAG)),
            "valid": True,
            "error": None,
        })
    for entry in entries:
        if entry.name.startswith("."):
            continue
        item: dict[str, object] = {
            "id": entry.name,
            "home": str(entry),
            "valid": False,
            "error": None,
        }
        try:
            validate_name(entry.name)
            context = resolve(root, entry.name)
            item.update(home=str(context.home), valid=True)
        except (OSError, ValueError) as exc:
            item["error"] = str(exc)
        result.append(item)
    return result
