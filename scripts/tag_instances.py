"""Discovery and atomic creation of independent Tag instance homes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unicodedata

try:
    from .tag_paths import data_home, home_migration_complete, native_installation, initialize_instance, initialize_workspace, workspace_home
except ImportError:
    from tag_paths import data_home, home_migration_complete, native_installation, initialize_instance, initialize_workspace, workspace_home


DEFAULT_TAG = "default"
SCHEMA_VERSION = 1
NAME_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?")
RESERVED_NAMES = frozenset({
    "add", "list", "memory", "settings", "inspect", "config", "setup",
    "reset", "migrate", "upgrade", "rollback", "version", "paths",
    "doctor", "start", "stop", "restart", "status", "logs", "dev",
    "telemetry", "rename", "describe", "autostart", "chatgpt", "usage", "abandon", "remove",
})


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

    @property
    def workspace(self) -> Path:
        return workspace_home(self.home, self.installation_root, self.tag_id)

    @property
    def is_main(self) -> bool:
        """Whether plain ``tag ACTION`` (no name) selects this Tag.

        A saved main Tag that still exists wins; ``default`` is main only otherwise,
        so exactly one Tag is ever reported as main.
        """
        saved = main_tag(self.installation_root)
        if saved and saved != self.tag_id and instance_path(self.installation_root, saved).exists():
            return False
        return self.is_default or saved == self.tag_id

    def command(self, action: str) -> str:
        target = "" if self.is_main else f"{self.tag_id} "
        return f"tag {target}{action}"

    def command_arguments(self, action: str) -> list[str]:
        return [action] if self.is_main else [self.tag_id, action]


def validate_name(name: str, *, allow_default: bool = True, existing: bool = False) -> str:
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        raise ValueError(
            "Workspace aliases must be 1-32 lowercase letters, digits, or hyphens"
        )
    if name == DEFAULT_TAG and not allow_default:
        raise ValueError("The alias 'default' is reserved for the built-in Tag")
    if name in RESERVED_NAMES and not (existing and name == "usage"):
        raise ValueError(f"The alias '{name}' is reserved for a Tag command")
    return name


def _main_record(installation_root: Path) -> Path:
    return installation_root.expanduser().absolute() / "state/main-tag.json"


def main_tag(installation_root: Path) -> str | None:
    """Return the saved main Tag, if it still names a valid alias."""
    try:
        record = json.loads(_main_record(installation_root).read_text(encoding="utf-8"))
        name = record.get("id") if isinstance(record, dict) else None
        return validate_name(name) if isinstance(name, str) else None
    except (OSError, ValueError, TypeError):
        return None


def set_main_tag(installation_root: Path, tag_id: str) -> None:
    validate_name(tag_id)
    path = _main_record(installation_root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".main-tag-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": SCHEMA_VERSION, "id": tag_id}, handle)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def select_unnamed(installation_root: Path) -> str:
    """Choose the Tag for commands given without a name.

    Prefers the saved main Tag, then a not-yet-renamed built-in Tag, then the
    only Tag. Falls back to the built-in name, which first-run setup creates.
    """
    main = main_tag(installation_root)
    # Discovery always lists the built-in Tag; only count Tags that exist.
    valid = [str(item["id"]) for item in discover(installation_root)
             if item["valid"] and Path(str(item["home"])).exists()]
    if main and main in valid:
        return main
    if DEFAULT_TAG in valid or len(valid) != 1:
        return DEFAULT_TAG
    return valid[0]


def workspace_name(home: Path) -> str | None:
    try:
        record = json.loads((home / "instance.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    name = record.get("workspace_name") if isinstance(record, dict) else None
    return name if isinstance(name, str) and name.strip() else None


def record_workspace_name(home: Path, name: str) -> None:
    """Remember the Slack workspace's display name beside the instance id."""
    path = home / "instance.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("workspace_name") == name:
        return
    record["workspace_name"] = name
    descriptor, temporary = tempfile.mkstemp(prefix=".instance-", dir=home)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def slugify(text: str) -> str:
    """Command-safe form of a display name: "Maya's Tag" -> "mayas-tag"."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    ascii_text = re.sub(r"['’]", "", ascii_text.lower())
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")[:32].rstrip("-")


def _record(home: Path) -> dict:
    try:
        record = json.loads((home / "instance.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def nickname(home: Path) -> str | None:
    value = _record(home).get("nickname")
    return value if isinstance(value, str) and NAME_PATTERN.fullmatch(value) else None


def _existing(installation_root: Path) -> list[InstanceContext]:
    contexts = []
    for item in discover(installation_root):
        if item["valid"] and Path(str(item["home"])).exists():
            contexts.append(resolve(installation_root, str(item["id"])))
    return contexts


def resolve_reference(installation_root: Path, reference: str) -> str:
    """Accept a Tag's ID or its nickname; IDs win, so a nickname never shadows one."""
    contexts = _existing(installation_root)
    if any(context.tag_id == reference for context in contexts):
        return reference
    for context in contexts:
        if nickname(context.home) == reference:
            return context.tag_id
    return reference


def set_nickname(installation_root: Path, tag_id: str, value: str) -> None:
    validate_name(value, allow_default=False)
    for context in _existing(installation_root):
        if context.tag_id != tag_id and value in {context.tag_id, nickname(context.home)}:
            raise ValueError(f"Another Tag already uses '{value}'; choose a different nickname with --nickname")
    home = resolve(installation_root, tag_id).home
    record = _record(home)
    if record.get("nickname") == value:
        return
    record["nickname"] = value
    descriptor, temporary = tempfile.mkstemp(prefix=".instance-", dir=home)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, home / "instance.json")
    finally:
        Path(temporary).unlink(missing_ok=True)


def suggest_name(installation_root: Path, workspace_name: str) -> str:
    """Return an available command-safe alias derived from a Slack workspace."""
    ascii_name = unicodedata.normalize("NFKD", workspace_name).encode("ascii", "ignore").decode()
    stem = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:32].rstrip("-")
    if not stem or stem == DEFAULT_TAG or stem in RESERVED_NAMES:
        stem = "workspace"
    candidate = stem
    number = 2
    while instance_path(installation_root, candidate).exists():
        suffix = f"-{number}"
        candidate = stem[: 32 - len(suffix)].rstrip("-") + suffix
        number += 1
    return candidate


def instance_path(installation_root: Path, tag_id: str) -> Path:
    validate_name(tag_id, existing=True)
    root = installation_root.expanduser().absolute()
    legacy = root / "instances" / tag_id
    candidate = data_home(root, tag_id)
    # Read-only commands and interrupted migrations keep using the old home
    # until the verified relocation checkpoint is committed.
    if candidate != legacy:
        for path in (candidate.parent, candidate):
            if path.is_symlink():
                raise ValueError(f"Tag instance path must not be a symlink: {path}")
        relocated = home_migration_complete(candidate)
        if not relocated and (legacy.exists() or legacy.is_symlink() or (
            tag_id == DEFAULT_TAG and (root / "config/settings.json").exists()
        )):
            candidate = legacy
    instances = candidate.parent
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
            raise ValueError(f"Unknown workspace alias '{tag_id}'. Run tag add first.")
        try:
            record = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            raise ValueError(f"Tag '{tag_id}' has malformed metadata") from None
        if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION or record.get("id") != tag_id:
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
        initialize_workspace(workspace_home(home, root, DEFAULT_TAG))
        metadata = home / "instance.json"
        if not metadata.exists():
            _write_metadata(metadata, DEFAULT_TAG)
        return resolve(root, DEFAULT_TAG)
    return _create(root, DEFAULT_TAG)


def _write_metadata(path: Path, tag_id: str, *, provisional: bool = False) -> None:
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "id": tag_id,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if provisional:
        # Renamed after its Slack name once setup chooses one (tag_rename).
        metadata["provisional"] = True
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def create(installation_root: Path, tag_id: str, *, provisional: bool = False) -> InstanceContext:
    validate_name(tag_id, allow_default=False)
    return _create(installation_root, tag_id, provisional=provisional)


def _create(installation_root: Path, tag_id: str, *, provisional: bool = False) -> InstanceContext:
    validate_name(tag_id)
    root = installation_root.expanduser().absolute()
    destination = instance_path(root, tag_id)
    instances = destination.parent
    instances.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"Tag '{tag_id}' already exists; its configuration was preserved")
    staging = Path(tempfile.mkdtemp(prefix=f".{tag_id}-", dir=instances))
    try:
        initialize_instance(staging)
        path = staging / "instance.json"
        _write_metadata(path, tag_id, provisional=provisional)
        try:
            os.rename(staging, destination)
        except OSError as exc:
            # macOS/Linux can report ENOTEMPTY instead of EEXIST when another
            # creator publishes a nonempty home after our existence check.
            if exc.errno in {errno.EEXIST, errno.ENOTEMPTY}:
                raise ValueError(f"Tag '{tag_id}' already exists; its configuration was preserved") from None
            raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    context = resolve(root, tag_id)
    try:
        initialize_instance(context.home)
        initialize_workspace(context.workspace)
    except Exception:
        # The destination did not exist before this creation attempt, so
        # restoring the pre-call state is safe and preserves atomic creation.
        shutil.rmtree(destination)
        raise
    return context


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
    if native_installation(root):
        # Discover copied/restored Tags directly from their working folders.
        # Deduplicate retained legacy originals by alias.
        by_name = {entry.name: entry for entry in entries}
        try:
            for entry in (Path.home() / "Tag").iterdir():
                if (entry / ".tag").exists() or (entry / ".tag").is_symlink():
                    by_name[entry.name] = entry
        except OSError:
            pass
        entries = sorted(by_name.values(), key=lambda item: (item.name != DEFAULT_TAG, item.name))
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
            validate_name(entry.name, existing=True)
            context = resolve(root, entry.name)
            item.update(home=str(context.home), valid=True)
        except (OSError, ValueError) as exc:
            item["error"] = str(exc)
        result.append(item)
    return result
