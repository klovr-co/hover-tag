# Changelog

All notable changes to Tag are documented here.

## Unreleased

### Added

- Install on Windows without Python. `install.ps1` now prepares Tag's own
  pinned Python with a checksum-verified uv, like `install.sh`, and installs
  the Slack CLI for Windows. Existing Windows installations move to the private
  Python automatically on the next `tag start`; the system Python is no longer
  used.

- Index Slack history through the MFS server's HTTP API instead of the `mfs`
  command-line client. Tag no longer downloads or needs the client on any
  platform, which also lets Windows finish setup; the separate check for it is
  gone from setup. Nothing changes for existing installations: their indexed
  memory and connectors stay as they are.

- Tag.app for macOS, Windows, and Linux, built with Tauri
  (`desktop/`). It installs Tag with the installer bundled in the app,
  showing structured progress, then lists, starts, renames, and adds Tags,
  shows each Tag's logs, upgrades Tag, and stays in the menu bar or system
  tray. Settings turn on **Open Tag at login** and **Keep Tags running**, and
  Tag.app notifies you when a Tag goes offline unexpectedly. It updates itself
  from signed releases on its own release line, when you choose **Restart to
  update**.

- Keep Tags running after login with `tag autostart on`. Tag remembers which
  Tags you started or stopped, and a per-user login service (launchd on macOS,
  systemd or XDG autostart on Linux, the Run key on Windows) starts them and
  restarts any that stop. `tag list --json` reports `keep_running`.

- Document the contract between the CLI and desktop apps
  (`docs/reference/app-protocol.md`). `tag version --json` reports the app
  protocol and capabilities, `tag NAME logs --json` returns recent service
  output, and `TAG_INSTALL_PROGRESS=jsonl` makes the installer report each
  step as structured progress.

- Choose who can use Tag from a searchable Slack people list during setup.
  Tag.app shows names, usernames, and profile photos; the CLI offers the same
  search with text labels. Manual member-ID entry remains available.

- Name every Tag after its Slack team and app IDs, for example
  `~/Tag/t0abc123-a0xyz789`, so several Tags can share a workspace or a Slack
  name without collisions. `tag add` no longer asks for an alias. Commands
  without a name use the main Tag. Existing installations rename their
  `default` Tag automatically on the next `tag start` or `tag setup`;
  `tag default …` keeps working.

- Rename a Tag in Slack with `tag NAME rename "New name"`, which also gives it a
  nickname for commands. Start, stop, or restart every Tag in a Slack workspace
  with `--workspace`, and see Tags grouped by workspace in `tag list`.

- Go back to the previous setup question in Tag.app or with
  `tag setup --step --back`, with the earlier answer selected. Back stops at
  steps that already changed something in Slack.

- Let apps drive guided setup without a terminal. `tag setup --json` and
  `tag add --json` ask the same questions as JSON lines, including a Slack
  sign-in step that shows the one-time line to send in Slack and accepts the
  code Slack returns.
  Every question has a stable `id`. Agents and scripts can instead run
  `tag setup --step`, `--answer`, and `--stop`, one question per command,
  while setup keeps running in the background.

- Connect a ChatGPT plan directly to each Tag with `tag chatgpt login`, including
  account selection, automatic token renewal, sign-out, and account-specific
  model choices. Existing installations retain their Codex sign-in until opted in.
- Run the Claude backend through the Claude Agent SDK with the same Slack
  behavior as Codex App Server: final-answer streaming, live activity rows,
  private one-time approvals, Stop, idle and maximum deadlines, and per-user
  model, thinking, and Fast Mode settings from the signed-in Claude account.
  `OPENTAG_CLAUDE_TRANSPORT=print` keeps the previous `claude -p` path as a
  rollback. Upgrades install the SDK automatically.
- Switch models between Codex and Claude from Slack's Configure control. The
  chosen model selects the backend for that user's next request, including
  mid-thread. `OPENTAG_DEFAULT_MODEL` sets each Tag's default model and
  `OPENTAG_BACKENDS` limits which signed-in backends are offered. Claude is no
  longer experimental.
- Show the agent, model, thinking level, and task duration above Configure
  on finished Slack replies, including stopped and failed requests.

- Deliver saved files as local copies plus Slack attachments by default.
  `OPENTAG_FILE_DELIVERY` and individual requests can select local-only delivery.
  Existing installations adopt the default automatically unless explicitly configured.
  Successful attachments keep one Open folder button and omit individual file buttons.

- Keep each Tag's working files and private settings, credentials, and state
  together in `~/Tag/NAME`, with automatic migration from older layouts. New
  saved deliverables default to `artifacts/CHANNEL_ID/`.
- Route supported Codex action approvals to private, one-time Slack controls for
  the original requester.
- Show failed requests privately with an error reference, retry, a sanitized
  report preview, and a local coding-agent repair prompt.
- Let requesters inspect bounded, redacted Codex tool activity through private
  Slack views after successful, failed, or stopped tasks.
- Offer optional, privacy-bounded CLI telemetry with an installation-wide
  preference and `tag telemetry` controls.
- Prepare pinned Python, uv, and Slack CLI dependencies during installation
  and upgrade without changing system Python or requiring a preinstalled CLI.

### Fixed

- Read and write Tag's files as UTF-8 on Windows. Windows used a legacy code
  page, so a saved Slack memory connector containing "—" never matched, and
  setup kept treating memory as unconfigured. Tag's launcher also runs Python
  in UTF-8 mode, which existing installations adopt on their next upgrade.

- Fix several Windows-only problems found by running the full test suite on
  Windows: renaming a Tag's folder failed while its start lock was open; the
  layout migration and upgrades misread untouched files written with CRLF
  line endings; `tag autostart` failed on accounts without a Run registry
  key; two agents recording output files at once could fail; and commands
  started without a terminal treated the null device as one and waited for
  input.

- Decode large Codex image events without repeatedly scanning the accumulated
  buffer, preventing avoidable transport timeouts.

- Keep local Open buttons for oversized output files and explain how to access
  them when they exceed the Slack upload limit.

- Include forwarded Slack files in attachment handling and keep downloaded
  filenames distinct when attachments share a name.
- Resolve explicit Slack channel mentions by ID when searching authorized,
  indexed channel history.
- Include already joined channels in setup and make Slack startup and indexing
  recover from rate limits more clearly.
- Resume automatic edge and prerelease publishing after a merge whose release
  processing failed or never ran, and start the `0.3.0` alpha line.

## [0.2.0] - 2026-09-22

### Added

- Stream Codex App Server answers and activity into Slack, with native Stop
  cancellation and direct-message invocation for authorized users.
- Search approved Slack channel history with caller and channel permission
  checks; deliver requested workspace files through private links and attach
  generated images to the originating thread.
- Provide guided, resumable Slack setup, app personalization, per-user Codex
  settings, and multiple independent Tags sharing one managed MFS service.
- Put normal-install agent workspaces under `~/Tag/NAME`, separate from
  application-managed data, and show non-blocking release update reminders.
- Add stable, beta, alpha, edge, and exact-version installation and upgrade
  paths with checksum and provenance checks, rollback safety, and a public
  release-channel index.
- Publish product guides covering setup, operation, access, integrations,
  capabilities, and troubleshooting.

### Fixed

- Recover interrupted Slack activity indicators, refresh the backend idle
  timeout on progress, accept bounded large completion events, and avoid
  indexing races during Slack channel invitations.
- Bundle the MFS CLI and migrate older workspace placeholders automatically.

## [0.2.0-beta.13] - 2026-09-22

- Enable history search for app-scoped Slack connectors.

## [0.2.0-beta.12] - 2026-09-22

- Clarify that stopping one Tag leaves shared memory running.

## [0.2.0-beta.11] - 2026-09-22

- Show the Tag CLI version in terminal headers.

## [0.2.0-beta.10] - 2026-09-22

- Clarify setup and startup readiness cues.

## [0.2.0-beta.9] - 2026-09-22

- Bundle the MFS CLI with the Tag runtime.

## [0.2.0-beta.8] - 2026-09-21

- Handle mentions immediately after channel invitations.

## [0.2.0-beta.7] - 2026-09-21

- Keep public release channels specific to their selected phases.

## [0.2.0-beta.6] - 2026-09-21

- Avoid GitHub API rate limits during public channel installs.

## [0.2.0-beta.5] - 2026-09-21

- Wait for invitation memory before reporting Slack readiness.

## [0.2.0-beta.4] - 2026-09-21

- Fix an invitation memory startup indexing race.

## [0.2.0-beta.3] - 2026-09-21

- Improve onboarding startup status cues.

## [0.2.0-beta.2] - 2026-09-21

- Make guided setup usable interactively and repair beta release preflight.

## [0.2.0-beta.1] - 2026-09-21

- Collect the v0.2 alpha work into the first beta candidate, including Slack
  streaming and search, guided setup, multiple Tags, and channel-aware upgrades.

## [0.2.0-alpha.12] - 2026-09-21

- Fix validation of automatically numbered alpha releases.

## [0.2.0-alpha.11] - 2026-09-21

- Add guided release management and retain stable as the default install channel.

## [0.2.0-alpha.10] - 2026-09-21

- Move user workspaces outside application data and add update reminders.

## [0.2.0-alpha.9] - 2026-09-21

- Recover stuck Slack working statuses.

## [0.2.0-alpha.8] - 2026-09-21

- Publish Tag product documentation and harden Slack setup and multi-Tag reset.

## [0.2.0-alpha.7] - 2026-09-21

- Support multiple independent Tags for separate Slack workspaces.

## [0.2.0-alpha.6] - 2026-09-21

- Bundle a Tag setup skill for guided installation and first-task verification.

## [0.2.0-alpha.5] - 2026-09-21

- Document the feature catalog issue workflow.

## [0.2.0-alpha.4] - 2026-09-21

- Personalize the Slack app identity during setup.

## [0.2.0-alpha.3] - 2026-09-21

- Accept bounded large Codex response events.

## [0.2.0-alpha.2] - 2026-09-21

- Add permission-aware cross-channel Slack search.

## [0.2.0-alpha.1] - 2026-09-20

- Launch guided Tag setup and lifecycle controls, with a persistent
  cross-platform installation home.
- Stream Codex answers into Slack; support direct messages, generated image
  uploads, private file delivery, and channel-aware upgrades.
- Harden alpha security, remove the hosted receiver, and simplify CI validation.

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

[0.2.0]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0
[0.2.0-beta.13]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-beta.13
[0.2.0-beta.12]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-beta.12
[0.2.0-beta.11]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-beta.11
[0.2.0-beta.10]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-beta.10
[0.2.0-beta.9]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-beta.9
[0.2.0-beta.8]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-beta.8
[0.2.0-beta.7]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-beta.7
[0.2.0-beta.6]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-beta.6
[0.2.0-beta.5]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-beta.5
[0.2.0-beta.4]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-beta.4
[0.2.0-beta.3]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-beta.3
[0.2.0-beta.2]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-beta.2
[0.2.0-beta.1]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-beta.1
[0.2.0-alpha.12]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-alpha.12
[0.2.0-alpha.11]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-alpha.11
[0.2.0-alpha.10]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-alpha.10
[0.2.0-alpha.9]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-alpha.9
[0.2.0-alpha.8]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-alpha.8
[0.2.0-alpha.7]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-alpha.7
[0.2.0-alpha.6]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-alpha.6
[0.2.0-alpha.5]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-alpha.5
[0.2.0-alpha.4]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-alpha.4
[0.2.0-alpha.3]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-alpha.3
[0.2.0-alpha.2]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-alpha.2
[0.2.0-alpha.1]: https://github.com/klovr-co/hover-tag/releases/tag/v0.2.0-alpha.1
[0.1.1-alpha]: https://github.com/klovr-co/hover-tag/releases/tag/v0.1.1-alpha
[0.1.0-alpha]: https://github.com/klovr-co/hover-tag/releases/tag/v0.1.0-alpha
