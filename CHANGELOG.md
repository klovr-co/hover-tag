# Changelog

All notable changes to Tag are documented here.

## [0.1.0-alpha] - Unreleased

### Added

- Slack mention handling backed by Codex CLI and scoped MFS memory.
- Guided macOS/Linux installation, a reusable Slack app manifest, and the
  portable `tag` setup/doctor/start/status/logs/stop command.
- Slack thread context, text attachments, Markdown conversion, long reply
  chunking, explicit channel posting, and Canvas creation.
- Optional Claude Code, native Zulip, and pinned ZulipMCP experimental paths.
- Apache-2.0 licensing and upstream Open Tag Example attribution.
- Cross-platform CI, clean-install smoke workflows, secret checks, and release
  metadata validation.

### Security

- Scoped MFS list/read/search helpers reject sibling-prefix and traversal paths.
- Transport-specific process environments reduce credential crossover.
- This alpha is explicitly limited to trusted sandbox use and is not a
  production security boundary.

[0.1.0-alpha]: https://github.com/klovr-co/tag/releases/tag/v0.1.0-alpha
