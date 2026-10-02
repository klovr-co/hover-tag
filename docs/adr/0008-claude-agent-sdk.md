# ADR 0008: Run Claude through request-scoped Agent SDK sessions

- Status: Accepted
- Date: 2026-10-02
- Related: [ADR 0004](0004-codex-app-server.md)

## Context

Tag's Claude backend used `claude -p --dangerously-skip-permissions`. Print
mode forwards every top-level text delta, including commentary written before a
tool call, and offers no tool lifecycle, approval channel, interruption, or
model catalog. The Slack bridge therefore treated Claude as a reduced backend:
no activity rows, no Stop confirmation, no Configure control, and no approvals.

## Decision

Tag runs Claude through the Python Claude Agent SDK by default, with one SDK
client and one session per Slack request. `OPENTAG_CLAUDE_TRANSPORT=print`
selects the legacy print transport as an explicit rollback.

The adapter in `scripts/claude_agent_backend.py` emits the same normalized
event contract as the Codex App Server adapter, so the Slack bridge does not
parse backend-native payloads:

- **Answer phases.** Claude does not label a message before writing it. Text
  deltas are buffered per API message and released as `final_answer` only when
  the stream reports a terminal stop reason; messages that stop for tool use
  become private `commentary`. The `ResultMessage` text is the authoritative
  final answer. Subagent text is never forwarded.
- **Activity.** Tool calls are translated into App Server item shapes and use
  the shared label and redacted-detail functions, so the public label
  vocabulary is unchanged. Planning and question tools stay private.
- **Permissions.** The default permission mode is `auto`, Claude's classifier
  analogue of Codex's automatic approval review. When Claude would still ask,
  `can_use_tool` sends an `approval_request` event and waits for the same
  file-based decision used by Codex; denial, timeout, Stop, or a missing
  approval channel fail closed. Interactive questions are declined.
  `OPENTAG_CLAUDE_PERMISSION_MODE` can select another non-planning mode.
- **Lifecycle.** The control file triggers `interrupt()` and waits for the
  interrupted result; the bridge's existing force-stop remains the fallback.
  SDK messages refresh the idle deadline; pending approvals and running tools
  also count as activity because Claude bounds each tool itself. The maximum
  deadline is never extended.
- **Settings.** Model discovery reads the `models` list from the SDK initialize
  response without starting a turn. Effort levels come from that catalog,
  Fast Mode is offered only where Claude reports support and is enabled with
  the `fastMode` setting, and the `default` entry lets the CLI choose. Choices
  are stored per backend; Codex keeps its original keys.
- **Configuration.** The operator's `claude` executable is preferred over the
  SDK's bundled CLI. User, project, and local setting sources load, and the
  workspace `.mcp.json` is layered explicitly, mirroring Codex's
  `.codex/config.toml` handling.

## Consequences

- `claude-agent-sdk` is a pinned runtime dependency. Upgrades reinstall
  `requirements-runtime.txt`, so existing Claude installations move to the SDK
  transport without manual steps; `tag doctor` reports a missing SDK.
- The final answer streams only after Claude finishes writing that message,
  rather than token by token, because its phase is unknown until then.
- Each request pays SDK and CLI startup cost, preserving the per-request caller
  and MFS scope isolation established by ADR 0004.

## Model-driven backend selection (2026-10-02)

The backend is no longer fixed per Tag. `OPENTAG_BACKEND` names the Tag's
default backend and `OPENTAG_DEFAULT_MODEL` (`backend:model` or `backend`) its
default model; setting the model keeps `OPENTAG_BACKEND` aligned. The bridge
discovers models from the default backend and from every other backend in
`OPENTAG_BACKENDS` (all by default) whose CLI is installed and signed in, and
the Configure picker groups them by backend. A saved choice stores its backend,
and the bridge runs each request on the requester's chosen backend. Choices
saved before this change carry no backend and are read as Codex, so no stored
data is rewritten. Claude is no longer labelled experimental.
