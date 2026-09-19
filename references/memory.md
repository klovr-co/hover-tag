# Memory And Optional Saved Notes

Tag's Memory is MFS retrieval from sources the operator has indexed and
authorized, such as Slack history, repositories, docs, issues, databases, object
stores, or web crawls.

The local helper stores small Markdown notes when a user explicitly asks Tag
to remember something. It also supports deterministic seed notes for demos.
It does not automatically save every conversation. The runtime instructions
describe when to call it and how to confirm saving and retrieval separately.

Default root:

```text
~/.mfs/opentag-memory/
```

Per-channel file:

```text
~/.mfs/opentag-memory/slack/<channel-id>/memory.md
```

Recommended shape:

```markdown
# Tag Memory: <channel-id>

## Preferences
- Answers should be concise.

## Decisions
- 2026-06-24: Use the engineering repo as one permitted demo source.

## Facts
- The current Slack channel is isolated for Tag validation.
```

Rules:

- Prefer indexing real permitted sources through MFS for realistic context.
- Keep seed notes factual and small. Do not paste full Slack threads or private
  customer details.
- After writing seed notes, run `scripts/opentag_memory.py sync` or pass `--sync`
  to `remember` to submit them for indexing. A returned job ID does not confirm
  that indexing is complete. Later retrieval also requires the note's source
  to be permitted by `MFS_ALLOWED_SCOPES`.
