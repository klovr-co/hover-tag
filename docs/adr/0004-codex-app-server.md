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

Only `final_answer` message deltas reach shared Slack replies. Unknown phases are buffered
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

## Account-aware model selection (2026-10-01)

Model discovery uses the installed Codex App Server's `model/list` response,
including its account-aware `isDefault`, before the bridge accepts requests.
It does not infer account availability or a default from shared cache priority:
other Codex clients can overwrite that cache with a different model catalog.
If discovery is unavailable, cached names may populate the picker but the
default model is left to Codex. Explicit operator defaults and user selections
are preserved when available; old unavailable user selections normalize against
the live catalog on every run without rewriting unrelated settings. Discovery
only initializes App Server and reads model metadata; it never starts or retries
an agent task.

## Activity-view extension (2026-09-29)

The bridge now stores bounded, redacted previews of documented App Server tool
inputs and results in a private per-run record. Slack replies for successful,
failed, and stopped runs contain an
Activity button, while its timeline and per-tool detail views open only for the
original requester after a fresh authorization check. Shared replies retain
sanitized activity labels and short tool identities. Private reasoning, commentary, and prompts remain
outside the activity record. Connector calls nested inside one tool step may
not appear as separate App Server items.

## In-conversation activity cards (2026-10-01)

The bridge maps tool lifecycle events to a grouped Slack activity block in the
streaming reply. Live row titles use the same bounded, redacted tool identities
as Activity. A small deterministic formatter presents them as
`Reading runtime-agent.md`, `Running script.py`, or `Updating app.py`.
It follows T3 Code's separation of command identity and display wording, using
an independent Python implementation without new dependencies or model calls.
Unrecognized commands retain their name rather than an inferred purpose.
The helper recognizes bounded shell chains and pipelines, common environment
and execution wrappers, Git subcommands, test/build/lint/typecheck/install
commands, file operations, and known connector verbs. Quoted arguments stay
within their command; inline program bodies, credentials and directory paths
never become row text. Dynamic shell constructs and ambiguous redirection use
a generic fallback instead of guessed intent.
Observed completions use past tense; failures, declined steps and interruptions
get explicit labels while the run is active. When the overall request succeeds,
all shared cards close as complete so recoverable tool errors do not leave the
Agent activity header red. Steps without a successful completion use neutral
“Finished” wording; their actual outcomes remain in the private Activity view.
Failed or interrupted requests retain their error/stopped states. Completed compound commands
use “Finished” because a successful shell exit cannot prove that every
conditional subcommand ran. No repeated thinking rows or extra detail messages
are added.
These short descriptions, including file basenames, are
visible to everyone in the thread. Full command arguments, directory paths,
inputs, results, and private reasoning remain outside the shared cards.
Repeated identities share one counted row; after eleven distinct rows, an
overflow row shows the latest additional step. Completion updates are batched
with the next start or final answer. The final shared status follows the request
outcome; per-tool diagnostic status remains in Activity. Successful answers use the
same stream; failure replies retain their private delivery.
The requester-only Activity view remains the place for redacted details. If task
cards are unavailable, ordinary answer streaming and final-reply fallback remain
available.

Slack requires one stream content mode from start through append and stop. A
task stream starts with `chunks`, so answer text and stream recovery also use
markdown chunks. The shared activity block shows short identities and counts.
Full redacted inputs and results are available through the requester-only
Activity button; no extra tool-details message is posted in the thread.
Failure replies place recovery, Configure (when relevant), and Activity buttons
in one actions block so the client can wrap them naturally without forced rows.


## Hide the diagnostic Activity button (2026-10-01)

Live Agent activity is the default progress UI. New successful, failed, stopped,
and attachment-error replies omit the separate Activity button. Keep bounded
activity recording, button builders, and requester-authorized modal handlers
intact. Button visibility is disabled; no user-facing developer-mode setting is
introduced yet. Existing Slack messages are not rewritten, and their old Activity
buttons still work for the original requester.


## Specific HTTP and Python activity targets

Shared command identities retain HTTP method, hostname, final URL path component,
and output basename for recognized curl invocations. URL credentials, query
parameters, fragments, headers, and payloads are excluded. Python execution
options no longer hide script basenames. Inline Python is parsed without execution
for explicit file operands and unambiguous literal filename assignments; these
are shown as “Running Python · filename”, not as an invented script name or outcome.
Unrecognized or dynamic operands keep the generic fallback. These identities also
keep unrelated HTTP/file work from collapsing into the same counted row.


Other recognized tools use the same compact target suffix: file listing and
metadata commands, text/JSON searches and transforms, Git file operations,
test runners and linters, package scripts, shell/TypeScript runners, and wget.
Only known option arities are parsed; search expressions, filters, configuration
values, and arbitrary unknown-command arguments are excluded. Inline file work
uses “Running Python · filename”; scripts retain “Running filename”.
