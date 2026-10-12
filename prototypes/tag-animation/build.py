#!/usr/bin/env python3
# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""Build the two prototype pages from their templates and the palettes.

    python3 prototypes/tag-animation/build.py

Writes animations.html and palettes.html next to this file. Each page is
self-contained, so it opens straight from disk with no server.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import palettes  # noqa: E402

PAGES = {"animations.html": "animations.template.html", "palettes.html": "palettes.template.html"}


def main() -> None:
    data = json.dumps(palettes.build(), separators=(",", ":"))
    for page, template in PAGES.items():
        html = (HERE / "src" / template).read_text(encoding="utf-8")
        html = html.replace("/*DATA*/", f"const DATA={data};", 1).replace("/*DEBUG*/", "", 1)
        (HERE / page).write_text(html, encoding="utf-8")
        print(f"wrote {page} ({len(html) // 1024} KB)")


if __name__ == "__main__":
    main()
