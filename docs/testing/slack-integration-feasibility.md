# Slack setup feasibility — 2026-09-19

Feature: [#13](https://github.com/klovr-co/hover-tag/issues/13).
Scope: official documentation, installed CLI help/version, and repository source.
No login, credential reads, app creation, workspace changes, or live API checks.

## Findings

| Proposed step | Evidence | Implementation consequence |
| --- | --- | --- |
| Prepare Slack CLI | Official Slack CLI v4.7.0 is already on this development machine. Tag's installer does not install it. | Detect and reuse; provide explicit installation when absent. Do not equate this machine with a fresh install. |
| Connect Slack | `slack login` supplies a slash-command authorization ticket; the user approves in Slack and returns a challenge code. | Show the real handoff, not a promised automatic browser OAuth redirect. Keep tickets/codes out of diagnostics, screenshots, issues, and persistent application logs. In agent-led setup, confine them to the active chat handoff the user selected, or use the private local-clipboard handoff. |
| Choose workspace | `slack auth list` lists authorized accounts. | Only offer authorized accounts, not every workspace the person might belong to. CLI help does not advertise JSON here; avoid assuming structured output. |
| Choose any existing app | `slack app list` lists teams where the project's app is installed. It is not a general manageable-app catalog. | Universal app discovery remains unverified. Offer known/linked apps and an explicit link-existing/manual path. Do not scrape private Slack endpoints to mimic the mock. |
| Link existing app | `slack app link` accepts an App ID, Team ID and environment, and saves project metadata. | Linking is not proof of management permission, compatibility, or successful installation. Validate separately. |
| Inspect configuration | `apps.manifest.export` requires an app-configuration token with the documented permission. | A bot token alone does not establish manifest-inspection access. Fall back to guided manifest review where unavailable. |
| Choose channels | `conversations.list` returns channels visible to the token and supports cursor pagination. | Multi-select is feasible; check membership and access per selected channel. Do not automatically join channels or expand scopes. |
| Run bridge | Socket Mode requires app-level connection credentials in addition to the bot credentials used for Web API calls. | CLI authorization is not sufficient evidence that the bridge is ready. Validate both layers. |

## Implementation update (working tree, 2026-09-19)

The sections below preserve the pre-implementation feasibility record. The
current working tree now implements the proposed orchestration in `tag setup`:
Slack CLI auth, approved create/link-by-App-ID, read-only compatibility checks,
separate Socket Mode/bot/history credential validation, joined-channel
multi-select, selected-ID-only connector generation, and current-channel MFS
scope narrowing. These paths have automated coverage but have not yet been run
against a real Slack sandbox. In particular, Slack CLI project linking, remote
manifest inspection permissions, actual indexing, and an observed reply remain
live acceptance questions—not established results.

## Existing repository support and gaps (at feasibility review)

Policy update 2026-09-20: the user explicitly replaced selected-only future
indexing with invitation-following memory. New onboarding discloses this approval;
existing completed setups remain selected-only unless opted in. A background
worker reconciles joined public/private channels and requests incremental MFS
syncs. It does not join unselected public channels. Live invitation/indexing
acceptance remains pending; synthetic worker tests do not establish a live sync.

Update 2026-09-20: new-app creation now uses approved `slack app install` with
Tag's local manifest hook and remote settings as the subsequent source of truth.
CLI-saved identity is recovered automatically. Pending installation is checked
separately; uncertain creation stops rather than repeating it. Local CLI hook
compatibility is tested, but live creation remains unverified (run R18).
Bot/Socket Mode credential entry is still a separate hidden-terminal step.

Later update 2026-09-20 (R22): automatic credential handoff is now implemented
through Slack CLI's custom deploy hook, with explicit installation-refresh
approval and separate bot/Socket Mode validation. Manual entry is a fallback.
Synthetic tests exercise the handoff and a real local helper process; live
credential handoff remains unverified. R20 records user-observed creation and
installation, superseding R18's unverified creation status.

- `scripts/slack_channels.py` already pages through public/private channel results
  using the bot token and labels membership. This code was inspected, not live-tested.
- Its current picker returns one channel or an empty value meaning any joined
  channel. That differs from the approved multi-select, selected-channels-only design.
- `scripts/opentag_setup.py` still requests tokens and permits generic MFS sources;
  Slack-only memory onboarding is not implemented by the HTML prototype.
- `slack-app-manifest.yaml` supplies the app settings and scopes. Requested scopes
  must be reviewed against actual features before installation; no changes made.
- No `slack.json` or `.slack` project integration was identified in the inspected
  source file listing. `slack project init` can change project metadata and Python
  dependencies, so do not run it in the user's source checkout as a diagnostic.

## Recommended real flow (original proposal)

1. Connect Slack: detect tooling; run the official authorization handoff with user
   interaction. Keep setup credentials out of backend runtime environments.
2. Choose an authorized workspace and create a Tag app, select a known linked app,
   or link an existing app from Slack app settings. Preserve manual connection.
3. Review required changes and obtain approval before app creation/update/install.
   Verify installation, app identity, Socket Mode credentials, and bot access.
4. List channels with the bot token. Select one or more, validate membership, and
   persist an explicit allowlist. Empty selection must never mean all channels.
5. Index only selected Slack history. Implement per-current-channel retrieval
   before claiming cross-channel isolation; ADR 0001's trusted-backend limitation
   still applies.
6. Check backend readiness, start managed services, and observe a real reply.

The app-picker prototype remains a design proposal. Do not mark F06/F08 or any
live test as passed on the basis of these documentation checks.

## Next sandbox checks

- Confirm official authorization and account selection with the intended user.
- In an isolated test project, exercise link-existing without modifying the app;
  establish which credentials/permissions can inspect its manifest.
- With approved bot credentials, list channels without reading messages; verify
  pagination, no visible channels, missing scopes, and membership failure.
- Obtain explicit approval before installing/updating an app, indexing history,
  or sending a message. Record actual evidence separately from this feasibility note.

## Official sources

- [CLI authorization](https://docs.slack.dev/tools/slack-cli/guides/authorizing-the-slack-cli/)
- [Login command](https://docs.slack.dev/tools/slack-cli/reference/commands/slack_login/)
- [Authorized accounts](https://docs.slack.dev/tools/slack-cli/reference/commands/slack_list/)
- [App installation list](https://docs.slack.dev/tools/slack-cli/reference/commands/slack_app_list/)
- [Link an app](https://docs.slack.dev/tools/slack-cli/reference/commands/slack_app_link/)
- [Export a manifest](https://docs.slack.dev/reference/methods/apps.manifest.export/)
- [List channels](https://docs.slack.dev/reference/methods/conversations.list/)
- [Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode/)
