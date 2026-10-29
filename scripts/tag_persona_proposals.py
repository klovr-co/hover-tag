# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""The Tag proposes a persona or a skill in a thread; the person who asked approves it (ADR 0012).

The agent records what it proposes in the run file (``tag_persona.py
propose-persona`` or ``propose-skill``). After the run, the bridge checks it
again, saves it under ``state/persona-proposals`` and posts a card. Nothing is
written until the requester, who must be allowed to use the Tag, approves it
on the card within an hour. A skill is text only (one ``SKILL.md``) and is
written only into the Tag's own workspace, never into a folder of yours.
Routines need no card here: the agent adds them with ``tag routine add``,
approved in the requester's name (#237).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

try:
    import tag_lead
    import tag_personas
    from tag_memory import write_document
except ImportError:
    from scripts import tag_lead, tag_personas
    from scripts.tag_memory import write_document

KINDS = ("persona", "skill")
SKILL_DESCRIPTION_LIMIT = 300
SKILL_BODY_LIMIT = 8_000
SKILL_FOLDERS = (".claude/skills", ".agents/skills")


class ProposalStore(tag_lead.ProposalStore):
    """The Tag-proposal store and its one-decision transitions, kept in this Tag's own folder."""


def store(home: Path) -> ProposalStore:
    return ProposalStore(home / "state" / "persona-proposals")


def validate_skill(record: dict[str, Any]) -> dict[str, str]:
    name = str(record.get("name") or "").strip()
    if not tag_personas.SKILL.fullmatch(name):
        raise tag_personas.PersonaError("Name the skill like a folder: lowercase letters, digits and hyphens, "
                                        "such as month-end-close")
    description = " ".join(str(record.get("description") or "").split())
    if not description or len(description) > SKILL_DESCRIPTION_LIMIT:
        raise tag_personas.PersonaError(f"Describe when to use the skill in up to {SKILL_DESCRIPTION_LIMIT} "
                                        "characters")
    body = str(record.get("body") or "").replace("\r\n", "\n").strip()
    if not body or len(body) > SKILL_BODY_LIMIT or "\x00" in body:
        raise tag_personas.PersonaError(f"A proposed skill is text of up to {SKILL_BODY_LIMIT} characters")
    if body.startswith("---"):
        raise tag_personas.PersonaError("Write the skill's instructions only; Tag adds the name and description")
    return {"name": name, "description": description, "body": body}


def skill_text(skill: dict[str, str]) -> str:
    return f"---\nname: {skill['name']}\ndescription: {skill['description']}\n---\n\n{skill['body']}\n"


def check(home: Path, proposal: dict[str, Any], *, workspace: Path | None, tag_name: str,
          tag_channels: list[str], allowed_scopes: list[str]) -> dict[str, Any]:
    """Validate a proposal against this Tag as it is now. Raises PersonaError with what to do instead."""
    kind = proposal.get("kind")
    if kind == "persona":
        persona = tag_personas.validate(proposal.get("persona") or {}, allowed_scopes=allowed_scopes)
        current = tag_personas.load(home)
        if tag_personas.find(current, persona.name):
            raise tag_personas.PersonaError(f"There is already a persona named {persona.name}")
        tag_personas.check_all([*current, persona], tag_name=tag_name, tag_channels=tag_channels)
        return {"kind": "persona", "persona": persona.to_json()}
    if kind == "skill":
        skill = validate_skill(proposal.get("skill") or {})
        if workspace is None:
            raise tag_personas.PersonaError("This Tag works in a folder of yours, where Tag adds no skills. "
                                            "Add the skill there yourself")
        if any((workspace / folder / skill["name"]).exists() for folder in SKILL_FOLDERS):
            raise tag_personas.PersonaError(f"A skill named {skill['name']} already exists")
        return {"kind": "skill", "skill": skill}
    raise tag_personas.PersonaError("Propose a persona or a skill")


def apply(home: Path, record: dict[str, Any], *, workspace: Path | None, tag_name: str,
          tag_channels: list[str], allowed_scopes: list[str]) -> str:
    """Write an approved proposal, checked again first; return what was added."""
    clean = check(home, record["proposal"], workspace=workspace, tag_name=tag_name,
                  tag_channels=tag_channels, allowed_scopes=allowed_scopes)
    if clean["kind"] == "persona":
        persona = tag_personas.validate(clean["persona"], allowed_scopes=allowed_scopes)
        tag_personas.save(home, [*tag_personas.load(home), persona])
        return f"Added the persona *{persona.name}*. Call it with `@{tag_name} {persona.name}: …`."
    skill = clean["skill"]
    for folder in SKILL_FOLDERS:
        target = workspace / folder / skill["name"] / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(skill_text(skill), encoding="utf-8")
    return f"Added the skill *{skill['name']}*. It's available from the next request."


def approval_text(record: dict[str, Any], *, tag_name: str) -> str:
    proposal = record["proposal"]
    expiry = f"This request expires in {tag_lead.APPROVAL_MINUTES} minutes."
    if proposal["kind"] == "persona":
        persona = proposal["persona"]
        lines = [f"*{tag_name} proposes a persona: {persona['name']}*", persona["description"]]
        if persona.get("skills"):
            lines.append("*Skills first:* " + ", ".join(persona["skills"]))
        if persona.get("voice"):
            lines.append(f"*Voice:* {persona['voice']}")
        lines.append(f"*Speaks up:* {persona.get('speaks_up') or tag_personas.DEFAULT_SPEAKS_UP}")
        lines.append(f"<@{record['requester']}>, approving adds this persona to {tag_name}. It shares "
                     f"{tag_name}'s permissions and memory. {expiry}")
        return "\n".join(lines)
    skill = proposal["skill"]
    preview = skill["body"] if len(skill["body"]) <= 1_500 else skill["body"][:1_500] + "…"
    return "\n".join([
        f"*{tag_name} proposes a skill: {skill['name']}*",
        f"*Use when:* {skill['description']}",
        "```" + re.sub("```", "ʼʼʼ", preview) + "```",
        f"<@{record['requester']}>, approving adds this text as a skill in {tag_name}'s own folder. {expiry}",
    ])
