# Memory

Tag has two kinds of durable context.

## Tag memory

Facts that people explicitly ask Tag to remember. Each Tag instance keeps one
memory for all channels and one memory per Slack channel under
`OPENTAG_MEMORY_ROOT` (default `<instance home>/state/memory`):

- `global.json`: entries for all channels.
- `channels/<CHANNEL_ID>.json`: entries for one channel.

Each scope file is one JSON document with `entries`, bounded change `history`,
and `forgotten` markers. Writes take the scope's lock, re-read the file, apply
the change, flush it to disk, and atomically replace the file, so concurrent
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
(`OPENTAG_CALLER_ID`) and channel (`OPENTAG_CURRENT_CHANNEL_ID`); the agent
chooses only the operation. Writes go to the current channel unless the agent
passes `--all-channels`, which the runtime instructions allow only when the
user explicitly asks for all channels. Changes must name the version they
replace; a stale version is refused. A change keeps the previous value in
history unless `--drop-old` is passed. `forget` removes the entry and its
history and keeps only who asked and when.

Successful writes append a receipt to `OPENTAG_MEMORY_RECEIPTS`. The Slack
bridge adds those receipts to the reply, or posts them in the thread when the
run fails, so the reported change never depends on the model's wording.

Claude auto memory and Codex memories are disabled for Tag runs
(`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, `-c features.memories=false`), so Tag
memory is the only place facts are kept between runs.

Forgetting does not change Slack messages or MFS indexes. A forgotten fact may
still be found in indexed Slack history.

## MFS retrieval

Retrieval from sources the operator has indexed and authorized, such as Slack
history, repositories, docs, issues, databases, object stores, or web crawls.
Add longer durable context as a real MFS source, then include that source root
in `MFS_ALLOWED_SCOPES`. The runtime uses `mfs_search.py`, `mfs_ls.py`, and
`mfs_cat.py` to retrieve only from those allowed roots.
