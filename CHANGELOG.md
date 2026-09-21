# Changelog

## Unreleased

### Added

- Manage independent workspace aliases for multiple Slack workspaces with `tag add`,
  `tag list`, and target-aware lifecycle, setup, settings, logs, and diagnostics.
- Share one explicitly owned MFS lifecycle across Tags, with per-Tag
  credential files and an administrative `tag memory` command.
- Give the built-in `default` Tag the same `instances/default` isolation and
  scrubbed runtime environment as every named Tag; existing root-level default
  data requires a one-time stopped-service migration.
- Let existing-app setup enable Slack's Agent messaging experience through a
  targeted, verified manifest sync while preserving unrelated app settings.

All notable changes to Tag are documented here.

## Unreleased

### Added

- Let new-app setup propose **&lt;the operator's first name&gt;'s Tag**, render a
  deterministic identity from 144 curated Tag waterdrop bases and 16 subtle signatures,
  or choose a validated local PNG,
  JPEG, or GIF profile picture for upload through Slack CLI.
- Mark test onboarding throughout the creation flow and prefix its proposed app
  name with `TEST ·`, since approved Slack operations in test mode remain real.

## [0.1.1-alpha] - 2026-09-18

### Security

- Apply one canonical URI scope policy to MFS list, read, and search helpers,
  rejecting sibling prefixes, raw traversal, and encoded traversal.
- Validate recorded process identity before reporting or stopping managed
  services, preventing stale PID files from targeting a reused PID.
- Document that agent backends inherit the bot, MFS, and ambient environment
  credentials; helper scope checks are guardrails, not a capability boundary.

### Fixed

- Let responsive agent tasks run beyond the former seven-minute wall-clock
  limit by separating the backend idle timeout from a one-hour maximum runtime.
- Make `tag status` fail when either required service is unhealthy.
- Wait for a live Slack Socket Mode connection during startup and print the
  bridge log tail when startup fails.
- Ignore tracked paths that have been deleted from the worktree during secret
  scanning.

### Removed

- Remove the legacy macOS `.command` launchers. `./install.sh` and `./tag` are
  now the only supported setup and service-control entry points.
- Remove the bundled Google Workspace skill catalog and project-local Gmail
  skills. Tag now ships only its core Slack, MFS, and backend integration.

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
