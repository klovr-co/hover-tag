#!/usr/bin/env python3
# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""The agent's persona helper: hand a thread over to a persona, or propose a persona or skill.

The bridge lists the personas a run may hand over to in ``OPENTAG_PERSONA_FILE``.
After the run, it posts one short handover line under the Tag's own name and
then the reply under the persona's name and picture, and the thread stays with
that persona (ADR 0012). A proposal is posted as a card after the reply, and
nothing changes until the person who asked approves it.
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
    persona = commands.add_parser("propose-persona", help="Propose a new persona for this Tag.")
    persona.add_argument("--name", required=True)
    persona.add_argument("--description", required=True, help="what it does, in one line")
    persona.add_argument("--picture", default="", help="a character name or a public https:// URL")
    persona.add_argument("--skill", action="append", default=[], help="a skill it reaches for first")
    persona.add_argument("--voice", default="", help="tone and length, up to 200 characters")
    skill = commands.add_parser("propose-skill", help="Propose a new skill (one SKILL.md of text).")
    skill.add_argument("--name", required=True, help="folder name, such as month-end-close")
    skill.add_argument("--description", required=True, help="when to use it")
    skill.add_argument("--body-file", required=True, type=Path, help="the instructions, or - for stdin")
    args = parser.parse_args(argv)
    destination = os.getenv("OPENTAG_PERSONA_FILE", "")
    try:
        if not destination:
            raise tag_personas.PersonaError("Personas are unavailable in this conversation.")
        if args.command == "propose-persona":
            clean = tag_personas.validate({"name": args.name, "description": args.description, "picture": args.picture,
                                           "skills": args.skill, "voice": args.voice})
            tag_personas.record_proposal(Path(destination), {"kind": "persona", "persona": clean.to_json()})
            print(f"After this reply, Tag will post the proposed persona {clean.name} for the person who asked to "
                  "approve. Nothing changes until they do; say so in your reply.")
            return 0
        if args.command == "propose-skill":
            try:
                import tag_persona_proposals
            except ImportError:
                from scripts import tag_persona_proposals
            body = sys.stdin.read() if str(args.body_file) == "-" else args.body_file.read_text(encoding="utf-8")
            clean_skill = tag_persona_proposals.validate_skill(
                {"name": args.name, "description": args.description, "body": body})
            tag_personas.record_proposal(Path(destination), {"kind": "skill", "skill": clean_skill})
            print(f"After this reply, Tag will post the proposed skill {clean_skill['name']} for the person who "
                  "asked to approve. Nothing changes until they do; say so in your reply.")
            return 0
        target = tag_personas.record_handover(Path(destination), args.to)
    except (tag_personas.PersonaError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Tag will post one handover line, then your reply as {target['name']}. Answer as "
          f"{tag_personas.brief_text(target)} Don't mention the handover in your reply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
