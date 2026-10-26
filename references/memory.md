# Memory

Tag has two kinds of durable context.

## Tag memory

Facts that people explicitly ask Tag to remember. Each Tag keeps its own
memory, never shared with another Tag, as markdown under `OPENTAG_MEMORY_ROOT`
(default `<instance home>/memory`):

- `core.md`: always-loaded entries for all channels, one `## key` section each.
- `notes/<key>.md`: one note for all channels.
- `channels/<CHANNEL_ID>/core.md` and `channels/<CHANNEL_ID>/notes/`: one
  channel's memory.
- `.entries.json` beside each `core.md`: who saved each section, from which
  thread, and which keys were forgotten (who asked and when, never the text).

Every file starts with front matter: `id`, `kind`, `written_by`, `from_thread`
(a `hover-tag://tag/<id>/thread/<channel>/<ts>` link) and `updated`. Entries
may link to each other with `[[key]]`. Writes take the memory lock, re-read the
files, apply the change, and atomically replace each file, so concurrent
requests cannot lose each other's changes.

Every entry has a key, a kind, the text, a version, and the Slack member who
saved it:

- `core` entries are always loaded. They share a hard limit of 6,000
  characters per scope; a save past the limit is refused, never truncated.
- `note` entries (up to 4,000 characters, 200 per scope) are listed by key in
  the prompt and read on demand.

`scripts/opentag_agent.py` adds the all-channels and current-channel memory to
every prompt. A channel entry hides an all-channels entry with the same key.
If memory cannot be read, the prompt says it is unavailable rather than empty.
Entries that look like instructions to override Tag's rules are refused when
saved and withheld when loaded.

Both backends use `scripts/tag_memory.py` (`list`, `get`, `history`, `search`,
`save`, `change`, `forget`). The runtime supplies the requester
(`OPENTAG_CALLER_ID`), channel (`OPENTAG_CURRENT_CHANNEL_ID`) and thread
(`OPENTAG_CURRENT_THREAD_TS`); the agent chooses only the operation. Writes go
to the current channel unless the agent passes `--all-channels`, which the
`tag-memory` runtime skill allows only when the user explicitly asks for all
channels. Changes must name the version they replace; a stale version is
refused.

### History

Every change is a commit in a private git repository outside the Tag's
working folder (`<installation>/state/knowledge/<id>.git`, written with
Dulwich; see [ADR 0011](../docs/adr/0011-markdown-memory-with-history.md)).
Each commit records the action, the entries, who asked, the Tag and the
thread. History keeps every value, and also each skill's `SKILL.md`.

The helper runs in the agent's sandbox and never writes history itself: it
appends each change to `.journal.jsonl`, and the Slack bridge commits it after
the run. `tag knowledge` commands do the same before they read history.
Changes made to the files by hand are recorded as made outside Tag.

`history KEY` prints the latest 10 earlier values. `forget` makes an entry
unreachable for the Tag, and the owner can restore it from history. `change
--drop-old` and `forget --drop-old` also remove the earlier text from history
and from the JSON backup.

### Upgrading from JSON memory

Tags before markdown memory kept `global.json` and `channels/<CHANNEL_ID>.json`
in `<instance home>/state/memory`. On first start (or first use), Tag turns
each scope into markdown files, and the last 10 values of each entry become
the first commits, with their original authors and times. The migration
checks that the always-loaded text is byte-for-byte the same for every
channel; if not, it stops and the JSON stays the memory. The JSON files stay
as a backup for one release.

Successful writes append a receipt to `OPENTAG_MEMORY_RECEIPTS`. The Slack
bridge adds those receipts to the reply, or posts them in the thread when the
run fails, so the reported change never depends on the model's wording.

Claude auto memory and Codex memories are disabled for Tag runs
(`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, `-c features.memories=false`), so Tag
memory is the only place facts are kept between runs.

Forgetting does not change Slack messages. A forgotten fact may still be found
by searching Slack.

## Slack search is not memory

Older Slack messages are not kept by Tag. When a request needs them, the
runtime reads Slack live with `slack_search.py`, inside the request's search
grant, and stores nothing it reads. See `references/runtime-agent.md` and
ADR 0012.
