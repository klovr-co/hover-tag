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
        return Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "Tag"
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
        return tag_home() / "instances/default"
    path = Path(override).expanduser()
    if not path.is_absolute():
        raise ValueError("TAG_INSTANCE_HOME must be an absolute path")
    return path


def tag_temp_dir() -> Path:
    """Return TAG's private temporary root, creating it for direct script runs."""
    home = instance_home()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = home / "tmp"
    temporary.mkdir(parents=True, exist_ok=True, mode=0o700)
    return temporary


def workspace_home(instance: Path, installation_root: Path, tag_id: str) -> Path:
    """Return the user-owned agent workspace for an instance.

    Normal installations keep editable work in ``~/Tag``. An explicit,
    non-standard TAG_HOME remains self-contained for development, testing, and
    portable installations.
    """
    root = installation_root.expanduser().absolute()
    if root.resolve(strict=False) == platform_tag_home().resolve(strict=False):
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
    for name in ("config", "integrations/bin", "state", "tmp"):
        (home / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    restrict_windows_acl(home)
    selected = Path(os.getenv("OPENTAG_WORKDIR", str(home / "workspace"))).expanduser()
    if selected.resolve(strict=False) == (home / "workspace").resolve(strict=False):
        initialize_workspace(selected)


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
    workdir = workspace or home / "workspace"
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


def codex_workspace_args(workdir: Path) -> list[str]:
    # CLI trust overrides do not activate project config in Codex 0.147.0.
    # Layer only TAG's MCP definitions explicitly, preserving global settings/auth.
    selected = Path(os.getenv("OPENTAG_WORKDIR", str(instance_home() / "workspace"))).expanduser()
    if os.getenv("TAG_HOME") and workdir.resolve() == selected.resolve():
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
    return []


def toml_value(value) -> str:
    if isinstance(value, dict):
        return "{" + ", ".join(f"{json.dumps(k)} = {toml_value(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    if isinstance(value, (str, bool, int, float)):
        return json.dumps(value)
    raise ValueError("MCP configuration contains an unsupported TOML value")
