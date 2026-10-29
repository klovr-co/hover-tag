# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""``tag [TAG] persona list|show|add|set|remove``.

Tag.app runs the same commands with ``--json``. Saving a persona here is the
operator's approval of exactly what was saved. A Tag's own agent can't change
personas from inside a request; Lead proposes them in Slack instead (ADR 0012).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import tag_display as display
    import tag_personas as personas_lib
except ImportError:
    from scripts import tag_display as display
    from scripts import tag_personas as personas_lib

ACTIONS = ("list", "show", "add", "set", "remove")
CHANGES = frozenset({"add", "set", "remove"})
# Until channel watching ships, a persona speaks only when called, whatever its level.
ACTIVE_LEVELS = frozenset({"only-when-asked"})
NOT_YET = ("Speaking up without an @mention needs channel watching, which this version doesn't have yet; "
           "until then every persona answers only when asked.")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="tag [TAG] persona", description=(
        "Personas are named faces over this Tag: a name, picture, description, skills, voice and how often it "
        "speaks up. They share the Tag's permissions, memory access and Slack app."))
    actions = root.add_subparsers(dest="action", required=True, metavar="|".join(ACTIONS))

    def command(name: str, help_text: str, *, reference: bool = True) -> argparse.ArgumentParser:
        sub = actions.add_parser(name, help=help_text, description=help_text)
        if reference:
            sub.add_argument("persona", help="the persona's name")
        sub.add_argument("--json", action="store_true", dest="json_output", help="structured output")
        return sub

    command("list", "List this Tag's personas.", reference=False)
    command("show", "Show one persona.")
    command("remove", "Remove a persona. Threads that were with it go back to the Tag's own name.")
    for name, help_text in (("add", "Add a persona."), ("set", "Change a persona.")):
        sub = command(name, help_text)
        if name == "set":
            sub.add_argument("--name", dest="new_name", help="rename the persona")
        sub.add_argument("--description", help=f"what it does, up to {personas_lib.DESCRIPTION_LIMIT} characters")
        sub.add_argument("--picture", help="a character (" + ", ".join(personas_lib.CHARACTERS)
                                           + ") or a public https:// URL")
        sub.add_argument("--skill", action="append", dest="skills", metavar="SKILL",
                         help="a skill it reaches for first; repeat for each" + ("; replaces the list" if name == "set" else ""))
        sub.add_argument("--no-skills", action="store_true", help=argparse.SUPPRESS if name == "add" else "clear the skill list")
        sub.add_argument("--memory-scope", action="append", dest="memory_scopes", metavar="URI",
                         help="limit its memory search to these allowed sources; repeat for each")
        sub.add_argument("--all-memory", action="store_true",
                         help=argparse.SUPPRESS if name == "add" else "search all of the Tag's memory sources again")
        sub.add_argument("--voice", help=f"tone and length in up to {personas_lib.VOICE_LIMIT} characters, "
                                         "such as 'warm, short replies'")
        sub.add_argument("--speaks-up", choices=personas_lib.SPEAKS_UP, help="how often it speaks up by default")
        sub.add_argument("--channel", action="append", default=[], metavar="CHANNEL=LEVEL",
                         help="per channel: C0123ABCD=when-addressed; =default removes the override")
    return root


def _record(args: argparse.Namespace, current: personas_lib.Persona | None) -> dict[str, Any]:
    record = current.to_json() if current else {"name": args.persona}
    if getattr(args, "new_name", None):
        record["name"] = args.new_name
    for key in ("description", "picture", "voice", "speaks_up"):
        value = getattr(args, key)
        if value is not None:
            record[key] = value
    if args.skills is not None or args.no_skills:
        record["skills"] = args.skills or []
    if args.memory_scopes is not None or args.all_memory:
        record["memory_scopes"] = args.memory_scopes or []
    channels = dict(record.get("channels") or {})
    for item in args.channel:
        channel, separator, level = item.partition("=")
        if not separator:
            raise personas_lib.PersonaError("Write each channel as CHANNEL=LEVEL, such as C0123ABCD=when-addressed")
        if level == "default":
            channels.pop(channel.strip(), None)
        else:
            channels[channel.strip()] = level.strip()
    record["channels"] = channels
    return record


def _view(persona: personas_lib.Persona) -> dict[str, Any]:
    return {**persona.to_json(), "picture_url": personas_lib.picture_url(persona.picture),
            "speaks_up_now": "only-when-asked"}


def _report(tag_id: str, tag_name: str, items: list[personas_lib.Persona]) -> dict[str, Any]:
    return {"schema_version": 1, "type": "personas", "tag": tag_id, "default_persona": tag_name,
            "personas": [_view(persona) for persona in items],
            "speaks_up_note": NOT_YET if any(
                level not in ACTIVE_LEVELS for persona in items
                for level in (persona.speaks_up, *persona.channels.values())) else None}


def _show(persona: personas_lib.Persona) -> None:
    display.header(persona.name, persona.description)
    display.info_row("Picture", persona.picture or personas_lib.DEFAULT_CHARACTER, good=True)
    display.info_row("Skills", ", ".join(persona.skills) or "any of the Tag's skills", good=True)
    display.info_row("Memory", ", ".join(persona.memory_scopes) or "all of the Tag's sources", good=True)
    display.info_row("Voice", persona.voice or "the Tag's usual voice", good=True)
    levels = [persona.speaks_up, *(f"{level} in {channel}" for channel, level in persona.channels.items())]
    display.info_row("Speaks up", "; ".join(levels), good=True)
    if any(level not in ACTIVE_LEVELS for level in (persona.speaks_up, *persona.channels.values())):
        display.info_row("Now", NOT_YET, good=False)


def run(home: Path, tag_id: str, arguments: list[str], *, values: dict[str, str],
        agent_run: bool = False) -> int:
    args = parser().parse_args(arguments)
    tag_name = values.get("OPENTAG_BOT_NAME") or tag_id
    if args.action in CHANGES and agent_run:
        print("tag persona can't change personas inside a Tag's request. Lead proposes a persona in Slack, "
              "and a person approves it.", file=sys.stderr)
        return 2
    try:
        items = personas_lib.load(home)
        if args.action == "list":
            if args.json_output:
                print(json.dumps(_report(tag_id, tag_name, items), indent=2))
                return 0
            display.header("Personas", f"{tag_name} answers as itself unless a persona is named or it hands over.")
            for persona in items:
                display.info_row(persona.name, persona.description, good=True)
            if not items:
                display.next_action("Add one", f'tag {tag_id} persona add Finance --description "Month-end close '
                                                'and invoices"')
            return 0
        current = personas_lib.find(items, args.persona)
        if args.action != "add" and current is None:
            raise personas_lib.PersonaError(f"{tag_name} has no persona named {args.persona}")
        if args.action == "show":
            if args.json_output:
                print(json.dumps({"schema_version": 1, "type": "persona", "tag": tag_id, "persona": _view(current),
                                  "speaks_up_note": _report(tag_id, tag_name, [current])["speaks_up_note"]},
                                 indent=2))
            else:
                _show(current)
            return 0
        if args.action == "remove":
            kept = [persona for persona in items if persona.key != current.key]
            personas_lib.save(home, kept)
            changed: personas_lib.Persona | None = None
        else:
            if args.action == "add" and current is not None:
                raise personas_lib.PersonaError(f"There is already a persona named {current.name}; "
                                                f"change it with tag {tag_id} persona set")
            allowed = [item.strip() for item in values.get("MFS_ALLOWED_SCOPES", "").split(",") if item.strip()]
            changed = personas_lib.validate(_record(args, current), allowed_scopes=allowed)
            kept = [persona for persona in items if current is None or persona.key != current.key] + [changed]
            channels = [item.strip() for item in values.get("SLACK_CHANNEL_IDS", "").split(",") if item.strip()]
            personas_lib.check_all(kept, tag_name=tag_name, tag_channels=channels)
            personas_lib.save(home, kept)
    except personas_lib.PersonaError as exc:
        if args.json_output:
            print(json.dumps({"schema_version": 1, "type": "error", "error": str(exc)}, indent=2))
        else:
            print(str(exc), file=sys.stderr)
        return 1
    if args.json_output:
        print(json.dumps(_report(tag_id, tag_name, personas_lib.load(home)), indent=2))
    elif changed is None:
        display.info_row("Removed", current.name, good=True)
    else:
        _show(changed)
    return 0
