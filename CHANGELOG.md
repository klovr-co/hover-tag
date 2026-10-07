# Changelog

All notable changes to Tag are documented here.

## Unreleased

### Fixed

- Tag.app's window no longer changes size as you move between the screens
  listed here. Installing Tag, adding a Tag, Home, Settings, AI connections and
  the usage data notice are all 616 pixels tall. Tag detail and launch loading
  keep their own heights. Longer content scrolls inside the window:
  the Tag list on Home, and the General tab in Settings. Shorter screens end
  with a quiet pixel river, and Settings › Updates and About also show
  "Built with ♥ by hover.team".

- Slack messages that send you to Tag.app no longer link to the unrelated
  tag.app website. "the Tag app" now opens that Tag's Details in Tag.app on
  the computer where it is installed, through the new `hover-tag://` link.
  The model error also names `tag NAME settings ai` for use without the app.
- Tag.app starts your new Tag as soon as setup finishes and greets it by the
  name you gave it, with your Slack name and picture on the example message.
  While it starts, Tag.app shows each step (Slack app, memory, reading its
  channels, connecting to Slack) and how long the current one has taken, and
  names the step that failed. `tag NAME start --json` reports those steps as
  JSON lines. Home shows the Tag as starting meanwhile.

- `tag start` no longer waits for memory to import a channel's Slack history
  before connecting, so a new Tag answers in seconds instead of failing after
  90 seconds when memory is busy. The import continues in the background, and
  history search covers each channel once it's imported. `tag start` now waits up to 90
  seconds for a setup, start or stop that is still running, instead of failing
  with "Another lifecycle operation is in progress". If that other operation
  already started the Tag, `tag start` reports it as running.

- When Slack refuses a rename, description change, or app settings update, the
  error now says what Slack reported, and suggests `slack login` only when
  Slack's message is about signing in.

- In Tag.app, a failed rename or description change shows under the field you
  were editing, which stays open so you can retry, instead of at the bottom of
  Home.

- Development builds of Tag.app (`./tag app`) no longer offer to update
  themselves to the latest release.

- Removing or resetting a Tag no longer fails with "MFS client is
  unavailable". Tag stopped installing the `mfs` client in 0.3.0, so it now
  removes the Tag's Slack history through the memory server's API. If memory
  isn't running, Tag finishes the removal the next time memory starts.

- Tag.app no longer says "Couldn't check for updates" for the few minutes
  after a release is published. Release channels for `tag upgrade` and the
  installers now move to a new release only after Tag.app has been built for
  every platform, so the app and your Tags update together. The channel now
  moves as soon as those builds are attached, instead of staying on the
  previous release. If a channel is still ahead of Tag.app, Settings says the
  release is being prepared and keeps your current version.

- Tag.app activity shows how many steps each request took (for example
  "7 steps") next to its time and model, and flags failed requests there. Click
  it to see the steps, replacing the separate "Request details" link.

- Slack replies no longer include Configure. Every requester uses the Tag's
  model and thinking level from Tag.app or the CLI; legacy per-user model,
  thinking, and Fast Mode overrides are archived automatically on startup.
  Historic buttons and open forms explain where settings moved.

- Tag.app activity now opens saved tool steps and matching error reports, with
  a link to the Slack thread and a cached AI-written, one-sentence summary of new delivered
  replies. The same details are available through
  `tag NAME logs --activity RUN_ID [--json]` for Codex and Claude. Channel names
  persist independently of memory settings and backfill automatically on startup
  for existing installations; successful lookups are reused across restarts.
- Tag.app now uses the connected bot's Slack profile picture, including for
  existing Tags without a locally saved setup picture. Startup backfills a
  versioned cache, running Tags refresh it hourly, and `tag list --json`
  reports the same cached image for both Codex and Claude. Failed downloads
  preserve the last good picture and retry automatically.

- Tags set up before Tag saved the Slack workspace's name now learn it on
  their next `tag start`, so Tag.app and `tag list` show "Klovr" instead of the
  workspace's Team ID.

- Preserve existing Tags named `usage` while reserving the alias for new Tags.
- Report invalid API setup settings without a traceback or environment mutation.
- Redact Basic authorization credentials in backend failure messages and omit
  the upstream API key from the gateway's Codex child environment.
- Record Codex cache-write tokens and price them separately when a cache-write
  rate is configured; otherwise report the estimate as unknown.

- Show bounded, redacted backend error messages when a failure does not match
  a known category, instead of “Cause not identified.” Extract messages from
  JSON errors without copying unrelated fields, and explicitly report when the
  backend provides no error message. This applies to both Codex and Claude.

### Added

- Optionally pin a Codex gateway provider with `provider.only` routing. A
  task-scoped authenticated loopback adapter adds the provider field while
  preserving streamed responses and upstream errors. Direct connections are
  unchanged when routing is unset; Claude and Azure routing are unsupported.

- Configure private API keys and custom base URLs for Codex and Claude, including
  Azure OpenAI Responses deployments for Codex. Record local token usage and
  estimated costs with `tag usage`, plus advisory monthly budgets per Tag.

- Tag.app shares the CLI's optional, privacy-bounded usage data. It shows the
  same notice before recording anything, and **Settings → Privacy → Share usage
  data** changes the one installation-wide choice that `tag telemetry` also
  controls. The app records a fixed set of events through
  `tag telemetry record`: opening, screens, setup steps, updates, and release
  channel switches. Background calls the app makes to the CLI no longer count as
  terminal use. See [telemetry](docs/reference/telemetry.md).

- Change a Tag's one-line Slack description after setup with
  `tag NAME describe "…" [--json]` (`""` clears it), or with **Edit** next to the
  description in Tag.app's Details tab. Slack is changed and verified first, so
  nothing changes locally if Slack needs a fresh sign-in. Tag details now show
  the description, and the Details tab is regrouped with Remove at the bottom.

- AI accounts are shared by all Tags and managed only in Settings → AI connections.
  Tag details and setup keep model choices per Tag. Existing per-Tag ChatGPT
  credentials migrate automatically; conflicting selections wait for an explicit
  shared account choice. CLI connection commands now have the same global scope.

- Tag.app has an Appearance setting (Auto, Light or Dark) in Settings. Auto
  follows the Mac, as before. The terminal has no appearance to set.
- Setting up a Tag starts with the Tag itself: its name, a one-line
  description, and its picture. Shuffle draws another waterdrop from any of
  the five elements, one your other Tags don't use, or you can upload your own.
  Then pick the AI, then the Slack workspace from the sign-ins you already
  have; Tag signs in to Slack only when it needs to. One recap shows what will
  be created, with Edit and Edit AI, before anything changes in Slack. The
  description appears on the app's Slack profile and in Slack's agent view.
  Choosing channels is optional: new Tags pick up channels they're invited to.
  `tag setup` in a terminal and Tag.app follow the same steps, and a setup
  paused in the old order picks up where it stopped.

- In Tag.app, Add a Tag shows these steps as a track in the sky with your new
  Tag as the marker: the picture, an @mention name and the description on one
  screen (Upload or drop an image), the model, your workspaces with
  organizations opening in place, one recap, and channels with search. Ready
  starts the Tag, copies a first message and opens the right place in Slack,
  and ticks only once the Tag has really replied. Settings → **AI & models**
  gets the same model picker and thinking level as Tag detail, and Change
  account opens as a dialog.

- Use an existing Slack app by picking it from the apps Tag knows (apps linked
  to this Tag, the Slack CLI's apps, and apps your other Tags use, which can't
  be picked twice), or paste its link. Tag shows what the app is missing, and
  **Update app** adds only those settings, keeping the rest.

- Tag.app has a new look: hover.team's sky and frosted panel, its navy main
  button, and Source Sans 3, by day and by night. Clouds, stars and the pixel
  Tags stand still when the system asks for less motion. First run greets you
  with Maya, shows install progress in the sky, and goes straight on to setting
  up your first Tag. Home shows your Tags' pictures in the header, one quiet
  line (a Tag whose AI can't answer and why, otherwise the latest reply, or a
  greeting), and two lines per Tag: its name with its model and thinking level,
  then what it's for. Updates run from the notice on Home, show their progress
  there, and offer Try again if one stops halfway. Settings is grouped into
  General, AI & models, Updates and About.

- Click a Tag on Home to open it, laid out like Slack: your workspaces in a
  rail, that workspace's Tags and channels in a sidebar, and the Tag's
  Activity (its recorded replies in each channel), Channels (with Open in
  Slack), and Details: its model and thinking level, which ask before
  restarting a running Tag, its mention, terminal command, working folder,
  Rename, and whether it's the main Tag. This replaces Logs and Home's ···
  menu. `tag list --json` adds each Tag's `channels`.

- Choose a Tag's default thinking level. `tag settings ai effort high` (or
  Settings → **AI & models** → **Change thinking level** in `tag settings`)
  saves it for the Tag's default model, and
  `tag settings ai model codex:gpt-5.5 --effort medium` saves a model and level
  together. Only levels the model offers are accepted. Codex and Claude both
  use it, and people's own choices in Slack still win. Changing the model keeps
  the level when the new model offers it and otherwise uses the new model's
  default. Existing Tags have no saved level and keep using each model's own
  default, so nothing changes until you choose one.

- Give a Tag a one-line description of up to 140 characters with
  `tag config set OPENTAG_BOT_DESCRIPTION "…"`. It's stored and shown in
  `tag list --json`; existing Tags and their Slack apps are unchanged.

- `tag list --json` adds each Tag's `description`, `default_model_name`, and
  `default_effort`, and `tag NAME logs --json` adds `activity`: the Tag's 50
  most recent Slack requests with when, where, and how each ended, from the
  activity records Tag already keeps. Prompts and people are never included.

- Connect an AI during setup, and manage it in Settings → **AI & models**.
  After the Tag's name and picture, setup asks for the Tag's default model
  from the models of the Codex and Claude accounts on this computer, grouped
  by agent, and offers to sign in to an agent that isn't connected. Only when
  nothing is connected does it list the agents, with sign-in or the install
  guide. One connected agent is required; both are optional. Settings shows each Tag's connections, Check connections, Sign in /
  Reconnect / Change account, and the default model, and asks before restarting
  a running Tag. Tag.app and `tag settings` offer the same choices, and
  `tag NAME settings ai … --json` gives apps browser sign-in with progress,
  cancel, and retry. Existing Tags keep their connections, default model, and
  people's own model choices in Slack.

- Choose the release channel in Tag.app: Settings now has **Release channel**
  (Stable, Beta, or Alpha). It shows what switching will do before anything
  changes, then moves the app and your Tags to that channel together and saves
  the choice for `tag upgrade` too. Switching to a channel that's behind your
  installed release keeps what you have until the channel catches up.

- Tag.app shows each Slack workspace's icon beside its name. Tag saves a local
  copy when setup finishes and on each `tag start`, and `tag list --json`
  reports it as `workspace_icon`. This uses Slack's `team:read` permission,
  which existing Tags request automatically on their next `tag start`. It's
  optional: if the workspace needs an admin to approve it, the Tag still
  starts, `tag start` says what to approve, and Tag asks again at most once a
  day. Until it's granted, and for workspaces without a custom icon, Tag.app
  shows just the workspace name.

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

- Setup no longer asks who you are from a list of everyone in the workspace:
  the person signed in to Slack becomes the Tag's owner, and if Slack can't
  say who that is, setup asks you to sign in to Slack again. Owners already
  saved are kept.

- Setup recognises an app that already has Slack's agent view, instead of
  reporting it missing every time.

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
