# ADR 0009: Continue one backend conversation per Slack thread

- Status: Accepted
- Date: 2026-10-07
- Supersedes: the "one ephemeral thread per Slack request" and "Threads are not
  reused between Slack requests" parts of [ADR 0004](0004-codex-app-server.md)
  and [ADR 0008](0008-chatgpt-plan-connection.md)
- Related: [ADR 0008](0008-claude-agent-sdk.md)

## Context

Tag started a new backend conversation for every Slack mention. Follow-ups in
one Slack thread lost the earlier turns' tool results and reasoning, and only
the last 30 Slack messages carried context forward. Tag.app showed every reply
as a separate Activity entry, and the operator's Codex and Claude history held
one untitled conversation per mention.

Codex ephemeral threads also break background sub-agents: Codex cannot load
the parent's context for the new agent, so `spawn_agent` fails with "failed to
load its thread context". Only ChatGPT plan connections used saved threads.

## Decision

- Codex task threads are always saved (`ephemeral: false`). Text-only reply
  summaries stay ephemeral and never resume.
- Each backend reports its conversation id with a normalized `session` event.
  The bridge saves it per Slack thread and backend in
  `state/agent-sessions.json` (`agent_sessions.ThreadSessions`) and passes it
  back to the next request in that thread as `--resume-session`.
- Codex resumes with `thread/resume`; Claude resumes with the Agent SDK
  `resume` option. A conversation that is missing, archived, or incompatible
  is replaced by a fresh one without failing the request.
- A stored conversation is continued only for the same Slack thread, backend,
  workspace directory, and requester, and only on the App Server and Agent SDK
  transports. The legacy exec and print transports never resume. Requesters do
  not share a conversation because its history holds tool results gathered
  under the first requester's Slack search grant.
- Cost limits start a fresh conversation instead of continuing one:
  `OPENTAG_THREAD_MAX_CONTEXT_TOKENS` (default 150000) bounds the conversation
  size each continued request re-reads, and `OPENTAG_THREAD_IDLE_HOURS`
  (default 4) starts fresh after the provider's prompt cache would have
  expired. `0` disables continuation. Backends report the size with a
  normalized `context` event taken from the latest main-thread model call, not
  the run's cumulative usage.
- Standing instructions are not repeated in the conversation. They go to Codex
  as thread `developerInstructions`, stored once in the thread, and to Claude as
  the system prompt, which is never part of the session history. Each request
  sends only what changes: its directories, search grant, question, and thread
  context, under "Request context".
- A continued conversation receives only the Slack messages posted after its
  last request, excluding Tag's own replies, which it already holds. When a
  resume fails, the fresh conversation receives the full thread excerpt.
- After each TL;DR, Tag titles the conversation with it: `thread/name/set` for
  Codex, `rename_session` for Claude. Naming is best-effort.
- Activity records keep the conversation id. `tag logs --json` reports an
  opaque `thread` key per run, and Tag.app groups runs that share it into one
  entry led by the latest reply.

## Consequences

- Every Tag request now appears in the operator's local Codex or Claude
  history, grouped and titled by Slack thread.
- Continued conversations grow with each request until a cost limit starts a
  fresh one. Each backend also compacts its own context near the model limit.
- One Slack thread runs one request at a time (`RunKey`), so two requests never
  write to the same conversation concurrently.
- No upgrade migration is needed. Existing records have no conversation id, so
  their next request starts a conversation that later requests reuse. Older
  Activity entries without a `thread` key stay ungrouped.
