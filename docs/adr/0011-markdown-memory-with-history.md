# ADR 0011: Keep each Tag's memory as markdown with a private history

- Status: Accepted
- Date: 2026-10-26
- Relates to: ADR 0001 (credential boundary), ADR 0007 (self-contained Tag
  folders)

## Context

Tag memory was one JSON document per scope, with the last 10 values of each
entry. People could not read it as a document, could not see who changed what
or from which thread, and could not undo a change from the app. A Tag's
owner should be able to open what their Tag knows, edit it, and undo any
change, including a change the Tag made for someone in Slack.

Each Tag has only its own memory and skills. Tags don't share knowledge, even
on one computer. A Tag still remembers only what someone asks it to.

## Decision

Memory is markdown in the Tag's private home, `<Tag home>/memory`:

- `core.md` is always loaded for all channels. Each entry is a `## key`
  section. The limit is unchanged: 6,000 characters per scope.
- `notes/<key>.md` holds one searchable note: up to 4,000 characters, and up
  to 200 notes per scope.
- `channels/<CHANNEL_ID>/` holds one channel's `core.md` and `notes/`.
- `.entries.json` beside each `core.md` records who saved each section and
  from which thread, plus which keys were forgotten (who asked and when, never
  the text).

Every file starts with a short front-matter header: `id`, `kind`,
`written_by`, `from_thread` (`hover-tag://tag/<id>/thread/<channel>/<ts>`) and
`updated`. Entries may link to each other with `[[key]]`. Skills stay where
the backends read them (`.claude/skills`, `.agents/skills`).

History is a bare git repository written with Dulwich, which Tag uses under
the Apache License 2.0, so nobody needs git installed. It sits outside the
Tag's working folder, at `<installation>/state/knowledge/<id>.git`. The id is
stored in `memory/.format.json`, so the history follows the Tag through a
rename. Every change is one commit. Its trailers record the action, the
entries, who asked (`Written-by`), the Tag, the thread and a `Change-Id`.
Undo is a new commit that puts back what one change replaced, entry by entry.
It is refused if that entry changed again since. History keeps every value;
the old limit of 10 values per entry is gone. History also keeps each skill's
`SKILL.md`.

The agent's memory helper (`tag_memory.py save|change|forget`) still makes
every agent write, with the same size limits and `UNSAFE_PATTERNS` checks.
It runs inside the agent's sandbox, which can't write outside the Tag's
folder, so it never touches the history. It writes the markdown and appends
the change, with the files it produced, to `memory/.journal.jsonl`. The Slack
bridge (after each run) and every `tag knowledge` command run outside the
sandbox and turn the journal into commits. A crash between the two is safe:
`Change-Id` keeps a repeated sync from committing twice. If the files changed
with no journal entry, for example because someone edited them by hand, the
next sync records that as a change made outside Tag. As in ADR 0001, this
makes changes evident, not impossible.

Forgetting removes the entry from what the Tag can reach, but the private
history keeps it so the owner can restore it from Tag.app. `--drop-old` on
`change` or `forget` removes the earlier text from history too. It rewrites
the commits that held it, deletes the old objects, and removes the text from
the JSON backup.

On first start, each JSON scope becomes markdown files, and its saved values,
up to the last 10 of each entry, become the first commits, with their
original authors and times. The always-loaded text must stay byte-for-byte
the same. The migration renders it from the JSON and from the markdown for
every channel and refuses to finish if the two differ. Until it finishes, the
JSON stays the memory. The JSON files stay in `state/memory` as a backup for
one release.

## Consequences

- People can read and edit memory as plain markdown, and see where each entry
  came from.
- Every change can be undone, and the undo is itself recorded.
- History lives outside the Tag's folder, so copying the folder does not copy
  history. `tag export` carries memory, history, skills, threads and settings.
- History needs the `dulwich` package. Without it, memory still works and the
  journal keeps every change until history can record it.
- A forgotten value stays in private history unless someone asks to remove
  it completely.
