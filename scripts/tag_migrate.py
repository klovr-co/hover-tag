"""Copy legacy checkout settings/skills into TAG home without moving originals."""
from __future__ import annotations

import os
import re
import shlex
import shutil
from pathlib import Path

try:
    from opentag_setup import write_config
except ImportError:
    from scripts.opentag_setup import write_config


def legacy_config(path: Path) -> dict[str, str]:
    tokens = shlex.split(path.read_text(encoding="utf-8"), comments=True)
    values = {}
    while tokens:
        if tokens.pop(0) != "export" or not tokens:
            raise ValueError("Legacy configuration must contain only export KEY=value statements")
        assignment = tokens.pop(0)
        key, separator, value = assignment.partition("=")
        if not separator or not re.fullmatch(r"(?:OPENTAG|SLACK|MFS)_[A-Z0-9_]+", key):
            raise ValueError("Unexpected legacy configuration key")
        # Parsing is deliberately non-evaluating: shell substitutions remain literal data.
        values[key] = value
    return values


def migrate(source: Path, home: Path) -> None:
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise ValueError("Migration source must be an existing checkout")
    config = home / "config/settings.json"
    if (source / ".env").exists():
        if config.exists():
            print(f"Keeping existing {config}")
        else:
            values = legacy_config(source / ".env")
            values["OPENTAG_WORKDIR"] = str(home / "workspace")
            write_config(config, values)
    for old, new in ((".codex/skills", ".agents/skills"), (".agents/skills", ".agents/skills"),
                     (".claude/skills", ".claude/skills")):
        directory = source / old
        if not directory.is_dir():
            continue
        for skill in directory.iterdir():
            target = home / "workspace" / new / skill.name
            if skill.is_dir() and (skill / "SKILL.md").is_file():
                if target.exists():
                    print(f"Keeping existing skill: {target}")
                else:
                    shutil.copytree(skill, target)
                    print(f"Copied skill: {target}")
    for relative in (".codex/config.toml", ".mcp.json"):
        original, target = source / relative, home / "workspace" / relative
        if original.is_file():
            # The initializer's comment-only placeholder contains no user settings.
            placeholder = target.exists() and target.read_text(encoding="utf-8") == "# TAG-only Codex MCP servers go here: [mcp_servers.NAME]\n"
            if not target.exists() or placeholder:
                shutil.copy2(original, target)
                if os.name != "nt":
                    target.chmod(0o600)
                print(f"Copied integration configuration: {target}; check any relative paths")
            else:
                print(f"Keeping existing integration configuration: {target}")
    print("Migration finished. Original files were preserved. Review tag doctor before starting.")
