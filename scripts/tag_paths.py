"""One platform-aware home for installed TAG data, independent of source trees."""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def tag_home() -> Path:
    override = os.getenv("TAG_HOME")
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ValueError("TAG_HOME must be an absolute path")
        return path
    if sys.platform == "win32":
        return Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "Tag"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Tag"
    data = Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    if not data.is_absolute():
        data = Path.home() / ".local/share"
    return data / "tag"


def initialize(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("releases", "config", "workspace/.agents/skills", "workspace/.codex",
                 "workspace/.claude/skills", "integrations/bin", "state", "tmp", "bin"):
        (home / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "nt":
        identity = subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"], text=True)
        sid = next(csv.reader([identity.strip()]))[1]
        subprocess.run(["icacls", str(home), "/inheritance:r", "/grant:r",
                        f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"],
                       check=True, stdout=subprocess.DEVNULL)
    config = home / "workspace/.codex/config.toml"
    if not config.exists():
        config.write_text(
            "# TAG-only Codex defaults and MCP servers go here.\n"
            "# model = \"gpt-example\"\n"
            "# model_reasoning_effort = \"high\"\n"
            "# service_tier = \"default\"\n"
            "# [mcp_servers.NAME]\n",
            encoding="utf-8",
        )


def runtime_environment(home: Path) -> dict[str, str]:
    """Keep backend authentication/global config intact; scope only TAG data."""
    return {
        "TAG_HOME": str(home),
        "OPENTAG_WORKDIR": str(home / "workspace"),
        "OPENTAG_MEMORY_ROOT": str(home / "state/memory"),
        "OPENTAG_SLACK_SETTINGS_FILE": str(home / "state/slack-user-settings.json"),
        "OPENTAG_SLACK_SESSIONS_FILE": str(home / "state/slack-active-sessions.json"),
        "TMPDIR": str(home / "tmp"), "TEMP": str(home / "tmp"), "TMP": str(home / "tmp"),
        "PATH": str(home / "integrations/bin") + os.pathsep + os.environ.get("PATH", ""),
    }


def codex_workspace_args(workdir: Path) -> list[str]:
    # CLI trust overrides do not activate project config in Codex 0.147.0.
    # Layer only TAG's MCP definitions explicitly, preserving global settings/auth.
    if os.getenv("TAG_HOME") and workdir.resolve() == (tag_home() / "workspace").resolve():
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
