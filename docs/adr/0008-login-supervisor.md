# ADR 0008: Keep chosen Tags running with a per-user login service

- Status: Accepted
- Date: 2026-10-02
- Amends: ADR 0003's deferral of automatic startup after login; ADR 0002's
  note that crash recovery needs an external supervisor.

## Context

Tags stopped when their machine restarted, and nothing restarted a bridge that
exited. The Swift Tag.app prototype restored Tags at login from its own preferences, so the CLI
and the app disagreed about which Tags should run, and Tags stayed off unless
the app was open. Windows and Linux had no equivalent.

## Decision

Each Tag records whether it should keep running in
`.tag/state/keep-running.json`. `tag start` sets it, `tag stop` clears it, and
`tag restart` keeps it. Keeping the record inside the Tag's private home means
it follows renames and folder copies.

`tag autostart on` registers one per-user service per installation that runs
`tag autostart run` through Tag's stable launcher: a launchd agent on macOS, a
systemd user service on Linux (an XDG autostart entry without systemd), and the
per-user `Run` key on Windows. No administrator rights are needed. The
supervisor checks every minute and starts wanted Tags whose bridge process is
gone, using the same `tag NAME start` readiness checks, with exponential backoff
up to an hour per Tag. When an upgrade selects a new release, it exits so the
service manager starts the new code.

Installations other than the platform-native home get a distinct service name,
so development and test homes never replace the real service.

## Consequences

- The CLI, Tag.app and the service share one source of truth.
- A misconfigured Tag that someone wants running is retried slowly, not dropped.
- Readiness failures that leave the bridge process alive are not restarted;
  `tag status` still reports them.
