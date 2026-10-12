# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""Quick add: answer a new Tag's setup questions when the workspace is already known.

``tag add --workspace T0ABC123`` runs the same setup as ``tag add``, but this
module reads its JSON-lines questions and answers the ones that have an
obvious answer: the suggested name and picture, the workspace given, the
default model, and approval to create the app, with no channels yet. Any
question without an obvious answer pauses setup with its progress saved, so
``tag NAME setup`` continues from there.
"""
from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Callable

TEAM_ID = re.compile(r"[TE][A-Z0-9]{6,}")


class Unanswered(Exception):
    """A setup question quick add can't answer; the message says why."""


def _match(target: str, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    wanted = target.strip().casefold()
    for entry in entries:
        if wanted in (str(entry.get("id", "")).casefold(), str(entry.get("name", "")).casefold()):
            return entry
    return None


def answer(event: dict[str, Any], *, workspace: str, name: str | None = None) -> Any:
    """The reply to one setup question, or Unanswered."""
    qid = event.get("id")
    if qid == "profile":
        return {"name": name or event.get("name") or "", "description": event.get("description") or ""}
    if qid == "default_model":
        return event.get("default", 0)
    if qid == "workspace":
        found = _match(workspace, event.get("workspaces") or [])
        if not found:
            known = ", ".join(f"{item['name']} ({item['id']})" for item in event.get("workspaces") or []) or "none"
            raise Unanswered(f"You aren't signed in to {workspace}. Signed in: {known}. "
                             "Run tag add to sign in to another workspace.")
        if found.get("kind") == "organization":
            raise Unanswered(f"{found['name']} is an organization; run tag add to pick its workspace.")
        return found["id"]
    if qid == "approve_setup":
        return "create"
    if qid == "channels" and event.get("allow_empty"):
        return []
    raise Unanswered(f"Setup needs your input ({event.get('prompt') or qid}).")


def drive(command: list[str], env: dict[str, str], *, workspace: str, name: str | None = None,
          forward: Callable[[dict[str, Any], str], None]) -> tuple[int, str | None]:
    """Run setup, answering its questions. Returns the exit code and why it stopped early, if it did.

    ``forward`` receives every event that isn't a question, with its raw line.
    """
    process = subprocess.Popen(command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               text=True, bufsize=1)
    reason: str | None = None
    try:
        for line in process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") != "question":
                forward(event, line)
                continue
            try:
                reply: dict[str, Any] = {"answer": answer(event, workspace=workspace, name=name)}
            except Unanswered as exc:
                reason = str(exc)
                reply = {"answer": None, "pause": True}
            process.stdin.write(json.dumps(reply) + "\n")
            process.stdin.flush()
    finally:
        process.stdin.close()
        process.stdout.close()
        code = process.wait()
    return code, reason
