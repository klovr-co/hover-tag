# Changelog

All notable changes to Tag are documented here.

## Unreleased

The 0.3 line introduces **Tag** as a desktop app for macOS. The
app for Windows and Linux is coming soon; the terminal installer works there now.
It also adds shared AI connections, Claude as a full agent backend, and Slack
threads that remember their conversation. Existing installations upgrade
automatically on their next `tag start` or `tag upgrade`. You don't need to set
anything up again.

### Desktop app

- **A desktop home for your Tags.** The app installs Tag, then lists, starts,
  stops, renames and adds Tags from the menu bar. It can open at
  login, keep your Tags running, and tell you when a Tag goes offline. It looks
  like hover.team: sky, frosted panel and Source Sans 3, by day and by night.
  The window stays one height on every screen.
- **Home** shows your Tags grouped by Slack workspace, each with its picture,
  model, thinking level and what it's for. A line at the top shows the latest
  reply or a Tag that needs attention. You can drag workspaces and Tags into
  your own order.
- **Tag detail is laid out like Slack.** Your workspaces are in a rail and
  their Tags and channels are in a sidebar. There are four tabs:
  - **Activity** shows each recorded reply, grouped by Slack thread, with the
    number of steps it took and an AI-written one-sentence summary. Open the
    steps to see what the Tag did.
  - **Channels** lists the channels the Tag is in, with Open in Slack.
  - **Logs** shows recent service logs that you can copy.
  - **Details** has the model, thinking level, description, mention, working
    folder, Rename and Remove.
- **Settings** has three tabs: General, Updates and About. General holds
  Appearance (Auto, Light or Dark), the usage data choice and a row that opens
  **AI connections**. Updates holds the release channel. You can replay
  onboarding from About.
- **Choose a release channel** (Stable, Beta or Alpha). The app shows what
  switching will do before it changes anything. It then moves the app and your
  Tags to the new channel together. Updates run from the notice on Home and
  show their progress there.
- **Open the app from Slack.** When a Slack message mentions "the Tag app", the
  link opens that Tag's Details in the app on the computer where the Tag runs.
- **Clear errors that tell you what to do next.** If the Tag list can't load,
  the app shows the real reason and clears the error once a retry succeeds. A
  Tag that can't reach Slack or memory says so and links to its Logs. If Tag is
  removed while the app is open, the app offers to install it again, then goes
  back to your existing Tags.
- **Microanimations.** Buttons give when you press them, lists slide, switches
  and progress bars move smoothly, and starting a Tag or getting a reply has a
  small moment of its own. All of this turns off when your system asks for
  reduced motion.

### Setting up a Tag

- **Setup starts with the Tag itself.** Choose its name, a one-line
  description and its picture. Shuffle draws a waterdrop that your other Tags
  don't use, or you can upload your own. Then pick the AI model and the Slack
  workspace. One recap shows everything before anything changes in Slack.
  Choosing channels is optional, because Tags also pick up channels they're
  invited to. The app and `tag setup` follow the same steps, and you can go
  back a step.
- **Your new Tag starts on its own.** When setup finishes, the Tag starts and
  the app greets it by name. The app shows each start step (Slack app, memory,
  reading channels, connecting to Slack), how long the current step has taken,
  and which step failed. `tag NAME start --json` reports the same steps.
- **Faster starts.** A new Tag answers within seconds, while it imports Slack
  history in the background. Two different Tags can start at the same time.
- **Reuse an existing Slack app** by picking it from the apps Tag knows, or by
  pasting its link. **Update app** adds only the settings that are missing.
- **Choose who can use Tag** from a searchable list of Slack people.
- **Channels you're already in** are included automatically. After the first
  successful start, Tag sends you a welcome DM with a first task to try.
- **Developer sandboxes and Enterprise organizations.** Pick an organization
  sign-in, then the workspace the Tag should work in. If an admin must approve
  the app, setup pauses and picks up where it left off.
- **Descriptions.** You can change a Tag's Slack description later with
  `tag NAME describe "…"` or with **Edit** in the app.
- **Workspace icons.** The app shows each Slack workspace's icon.

### AI connections and models

- **Shared AI connections.** All Tags share the same AI accounts, which you
  manage in Settings → General → AI connections. Each Tag keeps its own model choice.
  Existing per-Tag ChatGPT sign-ins move over automatically.
- **Claude is a full backend.** Claude runs through the Claude Agent SDK, with
  the same Slack behavior as Codex: streamed answers, live activity, private
  approvals, Stop and timeouts.
- **Thinking levels.** Choose a default thinking level for each Tag in the app
  or with `tag settings ai effort high`. Settings now belong to the Tag, so
  Slack replies no longer show Configure. Older per-person overrides are
  archived automatically.
- **API keys and gateways.** You can use your own API keys and custom base
  URLs for Codex and Claude, so Tags can use other AI providers and API
  gateways, including Azure OpenAI for Codex. You can also pin a gateway
  provider. `tag usage` shows token usage, estimated cost and
  optional monthly budgets.
- **Add your own API in the app.** Settings → AI connections → **Add your own
  API** takes Codex or Claude, a base URL, models and a write-only key, and
  applies it to all your Tags. Each API shows as `Codex · API (host)`; open it to
  edit, **Check connection** without spending tokens, or **Switch back to my
  plan**. In the terminal, `tag settings ai api set|check|clear` does the same
  in one step and restores the old settings if the Tag can't start.
- **ChatGPT plans.** Connect a ChatGPT plan with `tag chatgpt login`. Tag
  renews the token automatically and offers the models that account can use.
- **Your account's models.** Model pickers list the models your signed-in
  account can use. Finished replies show the agent, model, thinking level and
  how long the request took.

### In Slack

- **Threads remember the conversation.** Each Slack thread keeps one agent
  conversation, so follow-up mentions continue where the last reply stopped.
  This works for both Codex and Claude. A thread starts fresh after about four
  hours idle or when its context gets large. You can change both limits.
- **Earlier files stay reachable.** Tag can reopen files that were shared
  earlier in a thread, instead of guessing what they contained. It also keeps
  a copy of every image it generates in `artifacts/CHANNEL/images`.
- **Live activity in the thread.** While a Codex or Claude task runs, the
  thread shows short steps such as "Reading launch-plan.md", grouped and
  updated in place. Reasoning and full tool output stay private.
- **Approvals in Slack.** When Codex needs permission, the requester gets
  private choices: Allow once, Allow for this task, Deny, or Deny and stop.
  If Codex's automatic review blocks an action, you can approve one retry.
  Claude asks with Approve once or Deny.
- **Files arrive in Slack.** Files Tag saves for you are kept locally and also
  attached to the thread by default. Choose local-only for one request or with
  `tag config set OPENTAG_FILE_DELIVERY local`.
- **Private failure messages.** When a request fails in a channel, only the
  requester sees the cause, an error reference, and Retry, Fix with coding
  agent and Report issue. Reports stay on your computer. See
  [error reporting](docs/reference/error-reporting.md).
- **Background sub-agents.** A request finishes only after the sub-agents it
  started report back, so you get one complete answer.
- **Clearer failures.** When a failure has no known cause, Tag shows a short,
  redacted error message instead of "Cause not identified". When Slack refuses
  a rename or description change, Tag says what Slack reported.

### Running Tags

- **Keep Tags running** with `tag autostart on` or the matching setting in
  the app. A login service starts your Tags and restarts any that stop.
- **One folder per Tag.** Each Tag is named after its Slack team and app, for
  example `~/Tag/t0abc123-a0xyz789`, and keeps all its files there. Several
  Tags can share a workspace. You can control them together with
  `--workspace`.
- **No Python or Slack CLI to install.** The installers prepare a private,
  pinned Python and the Slack CLI on every platform, so Windows no longer
  needs Python.
- **No `mfs` client needed.** Tag indexes Slack history through the memory
  server's API on every platform.
- **Apps can drive Tag.** `tag setup --json`, `tag list --json` and
  `tag NAME logs --json` give apps and agents the same flows that people use.
  The protocol is described in `docs/reference/app-protocol.md`.
- **Usage data.** Tag shares anonymous, privacy-bounded usage data to improve
  setup and reliability. At the first run it turns this on and shows one short
  note; turn it off in Settings → Privacy or with `tag telemetry off`. Earlier
  opt-outs are kept. See [usage data](docs/reference/telemetry.md).

### Fixed

- Upgrades: when a restart fails during `tag upgrade`, Tag shows one clear
  error with `tag NAME doctor` for each failed Tag. Removing a Tag no longer
  fails with "MFS client is unavailable".
- The app: it no longer says "Couldn't check for updates" just after a
  release, because release channels move only after every app build is
  published. Development builds don't offer to update themselves. Rename and
  description errors show under the field you were editing.
- Setup: the person signed in to Slack becomes the Tag's owner. Setup now
  recognizes apps that already have Slack's agent view.
- Windows: Tag reads and writes its files as UTF-8, and several Windows-only
  problems with renaming, upgrades, autostart and terminal detection are
  fixed.
- Slack: forwarded files are included, attachments with the same name stay
  separate, explicit channel mentions resolve correctly, and Tag recovers
  better from rate limits. Files too large for Slack keep their local Open
  button.
- Security: Basic authorization credentials are redacted from error messages,
  and the gateway no longer passes the upstream API key to Codex.

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
