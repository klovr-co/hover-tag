#!/usr/bin/env python3
# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""The agent's persona helper: hand this thread over to one of the Tag's personas.

The bridge lists the personas a run may hand over to in ``OPENTAG_PERSONA_FILE``.
After the run, it posts one short handover line under the Tag's own name and
then the reply under the persona's name and picture, and the thread stays with
that persona (ADR 0012).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    import tag_personas
except ImportError:
    from scripts import tag_personas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hand this thread over to one of this Tag's personas.")
    commands = parser.add_subparsers(dest="command", required=True)
    handover = commands.add_parser("handover", help="Answer this request as another persona.")
    handover.add_argument("--to", required=True, help="the persona's name")
    args = parser.parse_args(argv)
    destination = os.getenv("OPENTAG_PERSONA_FILE", "")
    try:
        if not destination:
            raise tag_personas.PersonaError("Handing over is unavailable in this conversation.")
        target = tag_personas.record_handover(Path(destination), args.to)
    except tag_personas.PersonaError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Tag will post one handover line, then your reply as {target['name']}. Answer as "
          f"{tag_personas.brief_text(target)} Don't mention the handover in your reply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
