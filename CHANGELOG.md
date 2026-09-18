# Changelog

All notable changes to Tag are documented here.

## [0.1.1-alpha] - 2026-09-18

### Security

- Apply one canonical URI scope policy to MFS list, read, and search helpers,
  rejecting sibling prefixes, raw traversal, and encoded traversal.
- Validate recorded process identity before reporting or stopping managed
  services, preventing stale PID files from targeting a reused PID.
- Document that agent backends inherit the bot, MFS, and ambient environment
  credentials; helper scope checks are guardrails, not a capability boundary.

### Fixed

- Make `tag status` fail when either required service is unhealthy.
- Wait for a live Slack Socket Mode connection during startup and print the
  bridge log tail when startup fails.
- Ignore tracked paths that have been deleted from the worktree during secret
  scanning.

### Removed

- Remove the legacy macOS `.command` launchers. `./install.sh` and `./tag` are
  now the only supported setup and service-control entry points.

## [0.1.0-alpha] - 2026-09-09

### Added

- Slack mention handling backed by Codex CLI and scoped MFS memory.
- Guided macOS/Linux installation, a reusable Slack app manifest, and the
  portable `tag` setup/doctor/start/status/logs/stop command.
- Slack thread context, text attachments, Markdown conversion, long reply
  chunking, explicit channel posting, and Canvas creation.
- Native Slack loading states with a temporary-message fallback, plus optional
  real-time Claude answer streaming through a backend-neutral event protocol.
- Optional Claude Code experimental backend.
- Apache-2.0 licensing and upstream Open Tag Example attribution.
- Cross-platform CI, clean-install smoke workflows, secret checks, and release
  metadata validation.

### Security

- Slack invocations default to an owner-seeded user allowlist and fail closed
  when no authorized member ID is configured.
- Scoped MFS list/read/search helpers reject sibling-prefix and traversal paths.
- Bridge-only credentials are removed from backend process environments.
- This alpha is explicitly limited to trusted sandbox use and is not a
  production security boundary.

[0.1.1-alpha]: https://github.com/klovr-co/tag/releases/tag/v0.1.1-alpha
[0.1.0-alpha]: https://github.com/klovr-co/tag/releases/tag/v0.1.0-alpha
