"""Platform-aware installation and instance paths, independent of source trees."""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def platform_tag_home() -> Path:
    """Return the platform-native installation root without applying overrides."""
    if sys.platform == "win32":
        data = Path(
            os.getenv("LOCALAPPDATA", str(Path.home() / "AppData/Local"))
        ).expanduser()
        if not data.is_absolute():
            data = Path.home() / "AppData/Local"
        return data / "Tag"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Tag"
    data = Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    if not data.is_absolute():
        data = Path.home() / ".local/share"
    return data / "tag"


def tag_home() -> Path:
    """Return Tag's installation root.

    ``TAG_HOME`` remains the public installation-root override.  Named Tag
    instances must not reinterpret it as their private mutable home.
    """
    override = os.getenv("TAG_HOME")
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ValueError("TAG_HOME must be an absolute path")
        return path
    return platform_tag_home()


def instance_home() -> Path:
    """Return the explicitly selected mutable instance home.

    Child processes receive ``TAG_INSTANCE_HOME`` from the CLI.  Direct and
    direct script invocations select the built-in default instance.
    """
    override = os.getenv("TAG_INSTANCE_HOME")
    if not override:
        try:
            from .tag_instances import instance_path
        except ImportError:
            from tag_instances import instance_path
        return instance_path(tag_home(), "default")
    path = Path(override).expanduser()
    if not path.is_absolute():
        raise ValueError("TAG_INSTANCE_HOME must be an absolute path")
    return path


def tag_temp_dir() -> Path:
    """Return TAG's private temporary root, creating it for direct script runs."""
    home = instance_home()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    restrict_windows_acl(home)
    temporary = home / "tmp"
    temporary.mkdir(parents=True, exist_ok=True, mode=0o700)
    restrict_windows_acl(temporary)
    return temporary


def native_installation(installation_root: Path) -> bool:
    return installation_root.resolve(strict=False) == platform_tag_home().resolve(strict=False)


def data_home(installation_root: Path, tag_id: str) -> Path:
    """Preferred instance data location; legacy selection is handled by discovery."""
    if native_installation(installation_root):
        return Path.home() / "Tag" / tag_id / ".tag"
    return installation_root / "instances" / tag_id


def home_migration_complete(home: Path) -> bool:
    marker = home / "state/home-migration.json"
    if not marker.exists():
        return False
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("version") != 1:
            raise ValueError("unsupported checkpoint")
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"Cannot read Tag home migration checkpoint: {marker}") from exc
    return True


def default_workspace(home: Path) -> Path:
    if home.name == ".tag":
        return home.parent
    if home.parent.name == "instances" and native_installation(home.parent.parent):
        return Path.home() / "Tag" / home.name
    return home / "workspace"


def workspace_home(instance: Path, installation_root: Path, tag_id: str) -> Path:
    """Return the user-owned agent workspace for an instance.

    Normal installations keep editable work in ``~/Tag``. An explicit,
    non-standard TAG_HOME remains self-contained for development, testing, and
    portable installations.
    """
    root = installation_root.expanduser().absolute()
    if native_installation(root):
        return Path.home() / "Tag" / tag_id
    return instance / "workspace"


def initialize_workspace(workspace: Path) -> None:
    """Create the user-owned workspace and its backend configuration folders."""
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in (".agents/skills", ".codex", ".claude/skills"):
        (workspace / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    config = workspace / ".codex/config.toml"
    if not config.exists():
        config.write_text(
            "# TAG-only Codex defaults and MCP servers go here.\n"
            "# model = \"gpt-example\"\n"
            "# model_reasoning_effort = \"high\"\n"
            "# service_tier = \"default\"\n"
            "# [mcp_servers.NAME]\n",
            encoding="utf-8",
        )


def initialize(home: Path) -> None:
    """Create installation-wide paths, never instance-owned mutable data."""
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("releases", "instances", "shared/mfs", "state", "bin"):
        (home / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    restrict_windows_acl(home)


def initialize_instance(home: Path) -> None:
    """Create only app-managed directories owned by one Tag instance."""
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    if home.name == ".tag":
        if home.is_symlink():
            raise ValueError(f"Tag instance path must not be a symlink: {home}")
        home.chmod(0o700)
        ignore = home / ".gitignore"
        if not ignore.exists():
            ignore.write_text("*\n", encoding="utf-8")
    for name in ("config", "integrations/bin", "state", "tmp"):
        (home / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    restrict_windows_acl(home)


def restrict_windows_acl(home: Path) -> None:
    if os.name != "nt":
        return
    identity = subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"], text=True)
    sid = next(csv.reader([identity.strip()]))[1]
    subprocess.run(["icacls", str(home), "/inheritance:r", "/grant:r",
                    f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"],
                   check=True, stdout=subprocess.DEVNULL)


def runtime_environment(
    home: Path,
    *,
    installation_root: Path | None = None,
    tag_id: str = "default",
    workspace: Path | None = None,
) -> dict[str, str]:
    """Keep backend authentication/global config intact; scope only TAG data."""
    root = installation_root or home
    workdir = workspace or default_workspace(home)
    return {
        "TAG_HOME": str(root),
        "TAG_INSTANCE_HOME": str(home),
        "TAG_ID": tag_id,
        "OPENTAG_WORKDIR": str(workdir),
        "OPENTAG_MEMORY_ROOT": str(home / "state/memory"),
        "OPENTAG_SLACK_SETTINGS_FILE": str(home / "state/slack-user-settings.json"),
        "OPENTAG_SLACK_SESSIONS_FILE": str(home / "state/slack-active-sessions.json"),
        "TMPDIR": str(home / "tmp"), "TEMP": str(home / "tmp"), "TMP": str(home / "tmp"),
        "PATH": str(home / "integrations/bin") + os.pathsep + os.environ.get("PATH", ""),
    }


def codex_trust_args(workdir: Path) -> list[str]:
    """Trust only the Tag's folder, so Codex adds its `.codex/rules` (the Tag's own rules)
    to the operator's global rules. Codex 0.162 honours this; older versions ignore it."""
    return ["-c", "projects={ %s = { trust_level = \"trusted\" } }" % json.dumps(str(workdir.resolve()))]


def is_tag_workspace(workdir: Path) -> bool:
    """Whether `workdir` is the folder the running Tag works in, not some other project."""
    selected = Path(os.getenv("OPENTAG_WORKDIR", str(default_workspace(instance_home())))).expanduser()
    return bool(os.getenv("TAG_HOME")) and workdir.resolve() == selected.resolve()


def owns_workspace(workdir: Path, home: Path | None = None) -> bool:
    """Whether `workdir` is the Tag's own default folder. A Tag pointed at a folder of
    yours (OPENTAG_WORKDIR) gets no Codex rules or trust there, since they would also
    change how your own Codex behaves in that folder."""
    if home is None:
        if not is_tag_workspace(workdir):
            return False
        home = instance_home()
    return workdir.expanduser().resolve() == default_workspace(home).expanduser().resolve()


def codex_workspace_args(workdir: Path) -> list[str]:
    """Codex settings for the Tag's folder: trust (for its own rules) only in its own
    default folder, and its MCP servers; any other folder gets none. Connectors from
    Tag's shared list that this Tag may not use are switched off in every folder."""
    try:
        from . import tag_connectors
    except ImportError:
        import tag_connectors
    connectors = tag_connectors.codex_run_args(workdir)
    if not is_tag_workspace(workdir):
        return connectors
    return (codex_trust_args(workdir) if owns_workspace(workdir) else []) + _codex_mcp_args(workdir) + connectors


def _codex_mcp_args(workdir: Path) -> list[str]:
    # CLI trust overrides did not activate project config before Codex 0.162.
    # Layer only TAG's MCP definitions explicitly, preserving global settings/auth.
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    config = workdir / ".codex/config.toml"
    if not config.exists():
        return []
    with config.open("rb") as handle:
        servers = tomllib.load(handle).get("mcp_servers", {})
    arguments = []
    for name, definition in servers.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError("TAG MCP server names must use letters, digits, underscores or hyphens")
        arguments.extend(["-c", f"mcp_servers.{name}={toml_value(definition)}"])
    return arguments


def toml_value(value) -> str:
    if isinstance(value, dict):
        return "{" + ", ".join(f"{json.dumps(k)} = {toml_value(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    if isinstance(value, (str, bool, int, float)):
        return json.dumps(value)
    raise ValueError("MCP configuration contains an unsupported TOML value")
