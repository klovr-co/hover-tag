# ADR 0002: Verify identity and readiness for local services

- Status: Accepted
- Date: 2026-09-18

## Context

An operating-system PID can be reused after a process exits. A PID file alone
cannot prove that the current process is the service Tag started. Likewise, a
live bridge process does not prove that Slack Socket Mode is connected.

## Decision

For processes it starts directly, Tag records the PID, process start time, and
an expected command marker. It reports or signals a process only when all three
still match. The Slack bridge also writes a short-lived, instance-specific
heartbeat while the Slack SDK reports an active Socket Mode connection.

`tag status` succeeds only when MFS answers its health endpoint and the Slack
bridge identity and connection heartbeat are valid. `tag start` waits for that
same readiness condition and fails with the relevant log tail otherwise.

## Consequences

- Stale PID files fail closed instead of targeting an unrelated process.
- Process liveness and service readiness remain distinct checks.
- This is lightweight local supervision, not crash recovery; an external
  supervisor is still appropriate for unattended deployments.
