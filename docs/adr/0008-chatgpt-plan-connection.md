# ADR 0008: Authorize Tag's ChatGPT plan connection separately

- Status: Accepted
- Date: 2026-10-02
- Amended: 2026-10-05 — shared provider accounts for all Tags.
- Amended: 2026-10-07 — task threads are saved and continued per Slack thread; see [ADR 0009](0009-thread-scoped-agent-conversations.md).
- Tracking: [ChatGPT plan connection #153](https://github.com/klovr-co/hover-tag/issues/153)
- Extends: ADR 0004's inherited authentication and ephemeral-thread defaults.

## Decision

Existing installations continue to use their existing Codex sign-in. Operators
may explicitly connect a ChatGPT plan for all Tags with `tag chatgpt login`.
This uses OpenAI's dynamic public-client registration, PKCE, state, nonce,
and signature-verified OpenID Connect identity. It requires browser consent.
A valid identity without the plan scope is saved but cannot start inference.

The installation has one stable UUID host identifier, generated before first
consent and reused across restarts and sign-outs. All Tags share registrations, token sets, and one explicit active account in
`$TAG_HOME/shared/ai/chatgpt/accounts.json`. Settings manages provider connections;
Tag details and setup select only a model and thinking level. This supersedes
the original decision to keep independent accounts per Tag.
The issued client ID and verified subject identify a registration; email is
only display metadata. The account store has schema version 1. Its absence
means inherited Codex authentication and does not trigger consent or rewrite
existing settings. Host and account creation are idempotent and atomic.

Refresh happens under an OS-backed lock and commits the full rotating token
set together. Startup checks the selected grant before dependent services;
each Codex invocation reads fresh credentials. Terminal refresh errors clear
unusable tokens but retain the registration. Temporary errors preserve it.
Logout never silently selects a different billing path. Account changes require
all Slack bridges stopped, so its model catalog and task credentials stay aligned.
Account mutation commits share the installation and bridge startup locks and recheck the live
process after browser consent. Each task pins its authentication mode,
registration, and subject; renewal and usage-pause updates fail closed if that
identity changes. A failed model-catalog lookup logs the recovery error and
leaves the catalog empty without reading inherited Codex models; task startup
still validates the selected account before inference.

Codex receives the access token through its child environment and a custom
Responses provider using HTTP/SSE. Global Codex credentials are untouched.
The selected account's `/v1/models` response supplies visible model choices;
Tag chooses the first listed model when no available operator default exists.
The catalog is not proof that an inference request will succeed. Fast mode
and known unsupported hosted-tool features are disabled for this provider.
Local shell and MCP tools remain available, subject to provider capability.

Unlike legacy ephemeral requests, plan-connected requests use a unique saved
local Codex thread so a long task can renew its token. Near expiry, Tag requests
interruption and waits for its terminal acknowledgment. Only then does it
refresh, restart App Server, and resume that same thread. It retains the original
absolute task deadline and honors Slack Stop before resuming. It never replays
the original prompt after an unacknowledged interruption. Threads are not reused
between Slack requests. Local Codex history now retains these plan-connected
threads; OpenAI inference requests still use `store: false` and `stream: true`
through App Server's documented provider behavior.

## Consequences

- Codex must still be installed, but a plan connection needs no Codex login.
- All authorized Slack callers use the installation's operator-selected provider account.
  This is not per-Slack-user account linking.
- Account switching pauses all running Tags and restores that running set afterwards.
- Version 1 migration preserves old registrations and verifies shared storage before
  completion. Conflicting legacy account selections require an explicit shared
  selection; migration never guesses a billing identity. Legacy files remain for
  recovery but are not reimported. Models and thinking levels stay per Tag.
- Credentials remain subject to ADR 0001's trusted local-account boundary.
  Atomic writes and private permissions do not isolate them from a local agent.
- Credentials and the host identifier belong to the installation, outside new
  Tag folders. Older folders may retain migration copies of credentials. Moving
  to another host requires a fresh sign-in for that host before using the copied
  registration; do not clone the installation host identifier across machines.
- Browser consent and a completed live inference are manual release
  qualification; mocked authentication and transport tests cannot establish
  account eligibility or provider availability.

## References

- [Registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [Accounts and sessions](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [Codex App Server](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server)
- [Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
