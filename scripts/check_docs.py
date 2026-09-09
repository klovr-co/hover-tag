#!/usr/bin/env python3
"""Check local Markdown links and release-critical documented commands."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")


def markdown_files(root: Path) -> list[Path]:
    ignored = {".git", ".runtime", ".mypy_cache", ".codex", "skills"}
    return sorted(
        path
        for path in root.rglob("*.md")
        if not any(part in ignored for part in path.relative_to(root).parts)
    )


def validate_docs(root: Path) -> list[str]:
    errors: list[str] = []
    for document in markdown_files(root):
        text = document.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK_RE.findall(text):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            if target.startswith(("http://", "https://", "mailto:", "#", "/")):
                continue
            relative_target = unquote(target.split("#", 1)[0])
            if relative_target and not (document.parent / relative_target).exists():
                errors.append(
                    f"{document.relative_to(root)} links to missing path: {relative_target}"
                )

    readme = (root / "README.md").read_text(encoding="utf-8")
    for command in ("./install.sh", "./tag start", "./tag status", "./tag stop"):
        if command not in readme:
            errors.append(f"README.md does not document required command: {command}")
    return errors


def main() -> int:
    errors = validate_docs(ROOT)
    if errors:
        print("Documentation check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Documentation check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
