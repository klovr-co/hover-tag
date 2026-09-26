# ADR 0004: Stream Codex through request-scoped App Server processes

- Status: Accepted
- Date: 2026-09-20
- Tracking: [Response streaming #24](https://github.com/klovr-co/hover-tag/issues/24),
  [Task cancellation #29](https://github.com/klovr-co/hover-tag/issues/29)

## Context

Tag's Codex backend used `codex exec --json`. That interface reports completed
assistant messages but does not expose the incremental, phased answer and tool
lifecycle needed for truthful Slack streaming and cancellation. Backend
environments contain request-specific Slack caller identity and channel-narrowed
MFS scopes, so sharing a long-lived process across conversations would widen a
security-sensitive boundary.

## Decision

Tag uses local `codex app-server --stdio` by default, with one process and one
ephemeral thread per Slack request. `OPENTAG_CODEX_TRANSPORT=exec` selects the
legacy one-shot transport as an explicit rollback.

The adapter performs the initialize handshake, matches request IDs, keeps
stderr separate and bounded, routes item and turn lifecycle events, and cleans
up the process within bounded time. It uses workspace-write sandboxing with
automatic approval review. If the App Server still delivers a command, file,
or permission approval request to Tag, one-time Approve and Deny controls are
shown privately to the initiating user in the originating Slack thread. Only
that authorized user may decide the request;
stale, unavailable, or malformed requests fail closed. Other interactive
questions remain unsupported and are resolved conservatively. Global Codex
authentication, skills, settings, and workspace MCP overrides remain inherited.
Recognized item and turn lifecycle notifications refresh a bounded idle
deadline; they never extend the separate absolute task deadline. Slack status
refreshes are presentation-only and do not count as backend activity.

Only `final_answer` message deltas reach Slack. Unknown phases are buffered
until completion, commentary and reasoning remain private, and completed
messages are distinct from terminal turns. Activity labels are derived from
identifiable tool items. Slack Stop targets the active team/channel/thread/run
identity, sends `turn/interrupt`, waits for the interrupted terminal status,
then falls back to bounded process-tree cleanup if Codex is unresponsive.
The bridge explicitly moves Slack Agent Sessions between `processing` and
`active`; legacy assistant status calls remain a compatibility layer for
truthful custom activity copy where Slack supports it. Once answer streaming
starts, the stream owns the processing state until finalization.

## Consequences

- Each request pays App Server startup cost but preserves caller and MFS scope
  isolation and existing fresh-run history semantics.
- Existing Slack apps must add `assistant:write` and subscribe to
  `agent_session_stopped` before the native Stop control is available.
- Streaming delivery failures use the buffered answer from the same run; Tag
  never reruns a task merely to repair Slack delivery.
- Approval controls carry only request identity and a fixed action category;
  raw commands, paths, and permission payloads are not copied into Slack.
- Persistent Codex threads and cross-request App Server reuse are deferred until
  their isolation and history semantics are designed explicitly.
