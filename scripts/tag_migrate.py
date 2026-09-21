"""Copy legacy checkout settings/skills into TAG home without moving originals."""
from __future__ import annotations

import os
import re
import shlex
import shutil
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

try:
    from opentag_setup import write_config
    from tag_paths import initialize_workspace
    import tag_display as display
except ImportError:
    from scripts.opentag_setup import write_config
    from scripts.tag_paths import initialize_workspace
    from scripts import tag_display as display


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


def migrate(source: Path, home: Path, workspace: Path | None = None) -> None:
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise ValueError("Migration source must be an existing checkout")
    workspace = workspace or home / "workspace"
    initialize_workspace(workspace)
    display.header("Migrate", "Copying reusable settings and integrations into Tag home.")
    display.info_row("Source", display.short_path(source))
    display.info_row("Destination", display.short_path(home))
    display.section("Items")
    config = home / "config/settings.json"
    if (source / ".env").exists():
        if config.exists():
            display.info_row("Settings", "Kept existing file")
        else:
            values = legacy_config(source / ".env")
            values["OPENTAG_WORKDIR"] = str(workspace)
            with redirect_stdout(StringIO()):
                write_config(config, values)
            display.info_row("Settings", "Copied", good=True)
    for old, new in ((".codex/skills", ".agents/skills"), (".agents/skills", ".agents/skills"),
                     (".claude/skills", ".claude/skills")):
        directory = source / old
        if not directory.is_dir():
            continue
        for skill in directory.iterdir():
            target = workspace / new / skill.name
            if skill.is_dir() and (skill / "SKILL.md").is_file():
                if target.exists():
                    display.info_row("Skill", f"Kept {skill.name}")
                else:
                    shutil.copytree(skill, target)
                    display.info_row("Skill", f"Copied {skill.name}", good=True)
    for relative in (".codex/config.toml", ".mcp.json"):
        original, target = source / relative, workspace / relative
        if original.is_file():
            # The initializer's comment-only placeholder contains no user settings.
            placeholders = {
                "# TAG-only Codex MCP servers go here: [mcp_servers.NAME]\n",
                "# TAG-only Codex defaults and MCP servers go here.\n"
                "# model = \"gpt-example\"\n"
                "# model_reasoning_effort = \"high\"\n"
                "# service_tier = \"default\"\n"
                "# [mcp_servers.NAME]\n",
            }
            placeholder = target.exists() and target.read_text(encoding="utf-8") in placeholders
            if not target.exists() or placeholder:
                shutil.copy2(original, target)
                if os.name != "nt":
                    target.chmod(0o600)
                display.info_row("Integration", f"Copied {relative}", good=True)
            else:
                display.info_row("Integration", f"Kept {relative}")
    display.completion(
        "Migration finished",
        "Original files were preserved. Review copied integration paths before starting.",
        next_label="Verify the migrated installation",
        next_command="tag doctor",
    )
