# ADR 0010: Show thinking summaries in Agent activity

- Status: Accepted
- Date: 2026-10-13
- Supersedes: the "Private reasoning … remain outside the activity record",
  "No repeated thinking rows", and "private reasoning remain outside the shared
  cards" parts of [ADR 0004](0004-codex-app-server.md)
- Related: [ADR 0008](0008-claude-agent-sdk.md)

## Context

Tag showed tool steps while an agent worked but nothing while it thought, so a
long think looked like a stall. Both backends can return a provider-written
summary of the model's thinking instead of the raw text. Claude models from
Opus 4.7 on return no thinking text unless the client asks for
`display: "summarized"`. Codex App Server reports `reasoning` items with a
`summary` list beside the raw `content`, and `turn/start` takes `summary`.

## Decision

- Each stretch of thinking becomes a **Thinking** activity step. Its completed
  `details.output` is the provider's summary, bounded and redacted like other
  previews. Raw thinking and raw reasoning `content` are never forwarded.
- Claude passes `--thinking-display summarized` to the CLI and maps thinking
  content blocks from the stream. Codex sends turn `summary: "auto"` and maps
  `reasoning` items. Both share `THINKING_LABEL` and the `show_thinking()`
  policy in `scripts/agent_activity.py`; the bridge does not branch on backend.
- Slack's shared Agent activity shows the summary as the step's `details`.
  The first three Thinking steps get their own rows; later thinking joins a
  fourth counted row showing the latest summary, so tool steps keep theirs.
- The private Activity record keeps every summary as the step's result.
- `OPENTAG_SHOW_THINKING` (default `1`) is a per-Tag switch. At `0`, Tag stops
  asking either provider for summaries and drops any thinking it still gets.
- Text-only reply summaries and the legacy Codex exec and Claude print
  transports show no thinking.

## Consequences

Everyone who can read a Slack thread can read the thinking summaries. Owners
who want them private set `OPENTAG_SHOW_THINKING=0`. Summaries come from the
provider and may be empty when a model or gateway returns none; the step then
shows without details.
