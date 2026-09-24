# Setup test results

Scenarios: [setup-acceptance.md](setup-acceptance.md). Feature: [#13](https://github.com/klovr-co/hover-tag/issues/13).

## Run R01 — 2026-09-18 — assistant

- Surface: local HTML prototype, isolated Chromium via agent-browser.
- Artifact: `.context/setup-workflow.html`, SHA-256
  `a25800cadd1e5b0c4c167f8a3543be131e099f85d267f520913df52f6859e16a`.
- Workspace HEAD: `b970541f41a469d6586d0f58f2537800df72cc4f`;
  prototype is gitignored and is not part of that commit.
- Method: browser DOM `.click()` / `requestSubmit()` assertions via
  [setup-prototype-checks.js](setup-prototype-checks.js), including the real
  prototype progress timer. This is not pointer/keyboard or usability sign-off.
- No Slack calls, software installs, permission changes, indexing, or messages.
- Human acceptance: **Not tested for every scenario**.
- Live integration: **Not tested for every scenario**.

| ID | Prototype result | Observed evidence / limitation |
| --- | --- | --- |
| F01 | Pass (simulation) | New app → product channel → progress → connected; first reply remains unverified until simulated observation. |
| F02 | Pass (simulation) | TeamHelper and support channel preserved into progress. No actual app compatibility check. |
| F03 | Pass (simulation) | Repair guidance → recheck → channel retains TeamHelper. Token errors not modeled. |
| F04 | Pass (same-page simulation) | Blocked → saved → resumed with app/channel intact → progress. Process restart durability not tested. |
| F05 | Pass (simulation) | 7 days / Claude preserved on return and reopening settings. No actual backend authentication or execution. |
| F06 | Blocked | Static app list; no operator-role or permission-aware discovery fixture. GAP-01. |
| F07 | Fail (replay variant) | Approval wait/resume succeeds; fresh authorization afterward wrongly jumps to channel. BUG-01. |
| F08 | Blocked | No denied app, inaccessible channel, or empty-list state. GAP-02. |
| S01 | Not applicable | Prototype does not handle Slack callers. Requires live/instrumented verification. |
| S02 | Not applicable | Prototype does not index or retrieve memory. Requires live/instrumented verification. |
| X01 | Pass (simulation) | Manual connection illustration → checks → channel. No credentials collected. |
| X02 | Pass (DOM smoke only) | No-color mode toggles; action text remains present. Not a contrast/accessibility audit. |

Eight primary journeys: **5 simulated passes, 1 failure, 2 blocked**.
The approval subcase itself passed, as did the two auxiliary checks.

## BUG-01 — Stale approval state skips selection

- Status at R01: Open. Fixed and browser-DOM retested in R02 below.
- Reproduce: fresh page → Connect Slack → authorization completed → Example
  workspace → new app → administrator approval required → approval received →
  toolbar Start → Connect Slack → authorization completed.
- Expected: workspace picker, followed by app picker.
- Actual: channel picker directly (`state.scene === 'channel'`).
- Cause observed in source: `pendingFrom` retains `new-app`; the shared `approved`
  handler reuses it during later authorization. Start does not reset that state.
- Acceptance assertion: `Expected workspace after fresh authorization; got channel`.
- Retest: F07b in the smoke script; also run F01 and both approval paths.

## GAP-01 / GAP-02 — Permission scenarios unavailable

- F06 needs two operator roles with different app-management access.
- F08 needs unavailable discovery, no manageable apps, no joined channels, denied
  private-channel access, and revoked-access states.
- Static names and simulated green checks cannot establish permission enforcement.
- Do not mark the feature verified or the project item complete on this evidence.

## Additional limits

- Native browser Enter activation did not advance the focused Connect Slack
  control in this tool session. Cause was not established; classify native input
  testing as inconclusive, not a pass. DOM handlers were exercised separately.
- Two-prompt copy no longer describes all branches: workspace and app selection
  are additional choices. Human review should judge whether these are acceptable.
- Owner-only invocation is displayed; channel-visible replies need explicit
  disclosure before live setup acceptance.
- The read-only preview did not establish support for real Slack app enumeration.

## Run R02 — 2026-09-18 — assistant — BUG-01 retest

- Artifact SHA-256:
  `1f14e781990e9c78918d221e5ce72250c2a3090cd1632cfc801f3ed8cc993791`.
- Fix: clear stale approval context on Start/new authorization; consume approval
  context on completion and use it only on the pending-approval screen.
- Browser DOM regression results: F01–F05, F07a (new-app approval/resume),
  F07b (fresh-setup replay), F07c (authorization approval/resume), X01 and X02
  all passed. Original R01 failure is preserved above.
- Eight primary journeys: **6 prototype-navigation passes, 2 blocked** (F06/F08).
- BUG-01 is fixed for the tested prototype. Real Slack approval, native input,
  and human usability acceptance remain unverified. No live operations occurred.

## Run R03 — 2026-09-19 — assistant — multiple channels

- Artifact SHA-256:
  `38f2f7c31e9d7f04b7121229b7f64e075db1fa3784a10a315dbbada80ef733fd`.
- Browser DOM regression: F01–F05, all three F07 checks, X01/X02 passed.
  F06/F08 remain blocked; no live permission verification.
- M01 passed: select two channels, preserve selection through defaults, disable
  and guard submission with zero channels, remove channels from setup summary.
- Added S03 for current-channel-only memory retrieval. This is a proposed runtime
  requirement and remains Not tested; HTML copy does not enforce isolation.
- No real Slack authorization, indexing, messages, or runtime changes occurred.

## Run R04 — 2026-09-19 — assistant — implementation checks

- Surface: source implementation and automated tests; no HTML prototype result
  is counted as live evidence.
- Artifact: uncommitted working tree based on the workspace HEAD recorded for
  R01. Review the final Git diff when committing this run.
- Command: `./scripts/ci_check.sh` — **Pass, 111 tests**.
- No Slack login, credential read, app creation/link, permission change,
  indexing, or test message occurred.
- Human acceptance and live integration: **Not tested**.

| ID | Automated result | Live status / evidence still required |
| --- | --- | --- |
| F01 | Implementation covered | Run Slack CLI auth, approved app creation, multichannel setup, indexing, start, and observe a reply. |
| F02 | Implementation covered | Link a real App ID and confirm read-only manifest inspection and identity validation. |
| F03 | Partial | Missing requirements are reported without automatic mutation; approve and perform an actual repair, then rerun. |
| F04 | Pass (automated) | Resume tests preserve secrets/settings; process-level interruption during a real Slack approval remains untested. |
| F05 | Pass (automated) | Codex default and experimental Claude choice are preserved; no live backend invocation occurred. |
| F06 | Not implemented as discovery | Slack CLI cannot provide a universal manageable-app catalog; setup uses explicit App ID as the documented fallback. |
| F07 | Partial | Setup pauses for browser/admin approval, but a real admin-approval wait and replay remain untested. |
| F08 | Partial | Channel membership and CLI/API failures fail closed; restricted live roles/channels remain untested. |
| M01 | Pass (automated) | Multi-select requires at least one joined channel and persists explicit IDs; verify against real channel listings. |
| S01 | Pass (unit path) | Live instrumentation must still prove denial occurs before thread read, retrieval, and backend invocation. |
| S02 | Pass (configuration path) | Generated connector contains only selected IDs/window; inspect real indexed records and rejection evidence. |
| S03 | Pass (unit path) | Backend receives only the invoking channel's exact Slack scope; seed two real channels and test disclosure. |
| L01 | Pass (automated path) | Format validation/settings link exist; live Slack CLI link and access/compatibility checks remain untested. |

This run establishes that the implementation is ready for a controlled sandbox
attempt. It does not establish Slack service readiness or a successful reply.

## Run R05 — 2026-09-19 — workspace selection feedback

- Surface: user-reported real terminal setup, followed by automated regression
  checks on the uncommitted working tree.
- F01 partial observation: Slack CLI listed an authorized workspace, but setup
  then asked the user to copy its Team ID. User rejected that interaction.
- Fix: show numbered workspace choices, connect-another and exit actions;
  carry the selected ID internally and refresh accounts after real CLI login.
  Setup prompts now say workspace/channel without sandbox wording.
- Automated validation: workspace parsing, duplicate-account handling, invalid
  selection, login refresh, failed listing, and durable setup tests.
- User acceptance: Needs retest. No app creation, indexing, service readiness,
  or successful reply is established by this report.

## Run R06 — 2026-09-19 — real app-link failure

- Surface: user-supplied terminal transcript, followed by local regression checks.
- F01 workspace picker: user selected a listed workspace successfully.
- L01: **Fail**. Slack CLI rejected Tag's generated project with
  `invalid_app_directory` because `.slack/hooks.json` was absent. This failure
  does not establish missing app-management permission.
- Fix: create the missing hooks metadata for remote-manifest management while
  preserving existing project files. Also handle Ctrl-C in the parent CLI so
  pausing setup does not produce a traceback.
- Automated gate: Pass, 116 tests. L01 live retest: **Needs retest**.
- User also identified a broader UX gap: the real CLI does not yet implement
  the prototype's structured screens, channel checklist, optional defaults,
  automatic progress, and focused recovery flow. The workspace picker alone
  is not acceptance of that design.

## Run R07 — 2026-09-19 — terminal flow implementation

- User's subsequent live transcript confirms L01 app linking succeeded and the
  CLI reported the app installed. Compatibility then reported a missing
  `app_home_opened` subscription. This establishes linking, not reply readiness.
- The real terminal now follows Connect Slack → App → Channels → Finish, with
  keyboard menus, a Space-toggle channel checklist, optional history/agent
  defaults, an explicit scope/start approval, automatic startup, and focused
  retry/exit actions. Plain terminals use numbered input.
- App ID is saved before linking, and successful link metadata is saved before
  compatibility checking. F03/F04 recovery tests confirm retry/resume preserves
  the app and avoids a second link or unsolicited browser popup.
- F05 automated: changing to 7 days and experimental Claude returns to the
  summary, preserves app/channels, and regenerates the connector window.
- M01 automated: checklist toggling/removal and empty-selection guard pass.
  A real macOS pseudo-terminal exercised Down, Space, Enter, and restoration of
  normal terminal input. This is terminal-control evidence, not live Slack.
- Approval test: exiting the summary never writes the connector or starts Tag.
- Command: `./scripts/ci_check.sh` — **Pass, 123 tests**.
- Human acceptance of the updated flow: **Needs retest**. Live indexing, backend
  task success, S01/S02/S03, and first reply remain **Not tested**.

## Run R08 — 2026-09-19 — existing app link recovery

- User's live retry failed with `app_found`: Slack had saved the link before
  Tag's newer progress marker existed. Retrying linking could not resolve it.
- Read-only inspection confirmed Slack's `apps.dev.json` already contained the
  exact selected app/workspace pair. No live link or force operation was run.
- Fix: consult Slack's `apps.dev.json` and `apps.json` before linking and after
  a link attempt. Exact matches proceed to compatibility checks. Conflicting
  links are preserved and reported; a stale Tag marker cannot override them.
- Regression coverage includes old links without markers, both CLI metadata
  files, conflicting app IDs, and pause/resume after linking.
- Live recovery with updated code: **Needs retest**.

## Run R09 — installed service diagnosis and repair

- User approved repairing the installation launched by `~/.local/bin/tag`,
  located at `/Users/maxine/Coding/Tools/OpenTag/tag-cli` (a different checkout).
- Observed repeated `app_not_found` failures. Its app picker selected a deployed
  app, while startup always used `slack run`, which selects local apps.
- Stopped the restart loop. The installed service's existing shutdown routine
  also stopped its managed MFS server. No Slack permissions, app installation,
  history indexing, or messages were changed.
- Patched that installation: reject the unsupported startup before service
  installation; add hidden `tag settings --slack-credentials` validation that
  preserves settings; report Socket Mode readiness using a fresh heartbeat.
- Verification: 25 installed Tag tests passed, compile check and diff check
  passed. Startup now gives an actionable credential command instead of
  launching another restart loop.
- **Blocked on private terminal credential entry**: this installation has no
  saved bot/app tokens. Live Socket Mode connection and first reply remain
  unverified. No credentials were requested through chat.

## Run R10 — 2026-09-19 — missing-permission recovery and startup evidence

- Live user report: installed credential flow failed immediately after bot
  entry with `missing_scope`. User subsequently reported granting access.
  The old wrapper discarded Slack's method and `needed` scope details.
- Patched installed credential flow and workspace setup permission handling:
  display the failing check, reported scope and purpose; offer explicit app
  settings, retry with the same in-memory credential, or exit. Existing saved
  configuration is preserved. Installed-flow unsaved tokens are discarded on
  exit, explicitly explained; replacing a token requires rerunning the command.
- Automated only: installed tests 32 passed; workspace targeted tests 36 passed,
  1 skipped. Includes raw Slack error decoding, retry, explicit browser opening,
  exit without saving credentials, and token-output checks. Not live acceptance.
- User then ran start/status: process running, Slack unverified, MFS unhealthy.
  Read-only logs show `backend codex - codex executable missing` and an MFS
  health timeout. Shell resolves Codex at `/opt/homebrew/bin/codex`; generated
  launchd definition does not configure PATH. MFS status reports not running.
- No permission changes, indexing, service restart, or Slack messages performed
  during this run. Permission recovery needs live retest; successful Socket Mode
  connection and first reply remain unverified. Startup is still blocked.

## Run R11 — 2026-09-19 — installed background startup repaired

- User approved fixing executable discovery and restarting Tag. Changes were
  applied to the actual installed checkout at
  `/Users/maxine/Coding/Tools/OpenTag/tag-cli`, not a replacement installation.
- macOS service definition now captures the invoking terminal's absolute PATH
  entries, deduplicated, without copying other environment variables. No
  Homebrew-specific path is hardcoded. Regression failed before the fix and
  passed after it. Codex version check with saved service PATH succeeded.
- Live restart exposed a second launcher defect: `uv run --with slack-bolt`
  invoked an absolute Python outside its dependency environment. Changed it to
  `python` resolved by uv. Verified Slack library import without calling Slack.
- Installed targeted Tag suite: 30 tests passed; diff whitespace check passed.
- Following restart, actual `tag status` returned exit 0 twice with service
  running, Slack connected, and MFS healthy. Initial startup checks were not
  ready; only the subsequent all-ready status is counted as readiness evidence.
- F01/F02 service-readiness portion: observed pass. First reply, selected-only
  indexing and cross-channel memory scenarios are still unverified. No test
  message, explicit indexing operation, or Slack permission change was made.

## Run R12 — 2026-09-19 — terminal menu cleanup

- Simplified the installed home menu to Start/Stop, Settings, Troubleshoot,
  Update, and Exit menu. Removed deployment jargon from the home screen;
  Slack connection and memory health remain separate. Exit does not stop Tag.
- Grouped status, diagnostics, logs, and restart under Troubleshoot. Kept
  compatibility commands, including slack-run, callable. Command help now
  describes everyday commands; noninteractive bare invocation prints help.
- Workspace menu uses the same grouping with its available lifecycle actions;
  added a direct `tag settings` entry point. Update/restart parity is not claimed.
- Automated verification: 33 installed Tag tests and 17 workspace control tests
  passed. No live setup, restart, Slack mutation, indexing or test message was
  performed for this UI change. Human terminal UX review remains pending.

## Run R13 — 2026-09-19 — command-first entry point

- Plain `tag` now prints a short status and next action without opening a menu
  or launching setup, in both the workspace and installed checkout. Explicit
  `tag menu` retains the interactive interface; setup/settings remain interactive.
- Installed tests: 35 passed; workspace control tests: 17 passed. Bare invocation
  on an unconfigured temporary home did not create configuration. Tests cover
  stopped, unhealthy, and ready next-command selection in the installed version.
- Live bare invocation returned promptly: Slack not connected or unverified,
  memory ready, next command `tag doctor`. This is current readiness evidence,
  not a successful reply. No restart or external mutation performed.

## Run R14 — 2026-09-19 — remove redundant main menu

- Removed the `menu` command and unused home/troubleshooting navigation from
  both installed and workspace CLIs. Plain `tag` still shows the summary;
  setup/settings remain interactive. Existing direct service/diagnostic commands
  are unchanged. Earlier R12/R13 menu behavior is superseded.
- Verification: 31 installed Tag tests and 16 workspace control tests passed,
  including rejection of the removed command and preservation of bare-command
  summary dispatch. No service restart or Slack actions were performed.

## Run R15 — optional Codex diagnostic report

- Added report preview and explicit default-no consent after interactive doctor.
  No raw logs or configuration are forwarded; known error strings map to fixed
  historical categories. No automatic repair/restart behavior was added.
- Mocked tests cover sanitized reports, decline/noninteractive paths, read-only
  invocation and environment filtering, and unsupported Codex safeguards.
- 37 installed Tag tests and 20 workspace diagnosis/control tests passed.
  Local Codex help/features were inspected; no live model diagnosis was run.
  Current broken-pipe cause and successful reply remain unverified.

## Run R16 — 2026-09-20 — staged onboarding isolation

- Workspace-generated Slack connector files now live in
  `<TAG_HOME>/integrations/mfs/connectors`, not the shared `~/.mfs/connectors`.
  Existing saved connector paths are not migrated or overwritten automatically.
- Added setup-script `--no-start`: validates/saves onboarding choices but skips
  service startup and indexing. It does not suppress Slack authorization or app
  approval prompts, and is not a simulation.
- Automated verification: 25 setup tests and 17 control tests passed, including
  separate-home connector preservation, invalid workspace path rejection, file
  permissions, and skipping the service finish step.
- No working configuration was moved, no service stopped, and no live test
  app, connector or test home was created. App/workspace/channel choices await
  the user. End-to-end isolation remains incomplete: MFS client routing and a
  separate server must be configured before allowing the test's start/indexing.

## Run R17 — 2026-09-20 — Questionary terminal controls

- Surface: workspace implementation and real local pseudo-terminal tests;
  not the HTML prototype or live Slack integration.
- Replaced handwritten terminal selectors with Questionary 2.1.1. Workspace,
  app, retry and defaults choices share a select control; channels use a checkbox
  control. Completed answers collapse, with spacing between prompts. Numbered
  non-TTY input and NO_COLOR remain supported.
- M01 control-level checks pass: initial selection, selected indices, removal
  and empty-selection validation. Actual Down/Space/Enter input was exercised.
- F04 control-level checks pass: q and Ctrl-C pause and restore terminal input.
  This does not establish a new live process-resume acceptance result.
- Verification: `./scripts/ci_check.sh` passed, **147 tests**. Includes actual
  terminal default selection and cancellation, plus mocked prompt adapters.
- The isolated test launcher now supplies Questionary through uv and retains
  its existing TAG_HOME and --no-start flag. Installed working checkout,
  credentials, services, Slack permissions and indexing were not changed.
- Human visual acceptance: **Needs retest**. Live Slack acceptance and observed
  first-reply status are unchanged by this UI work.

## Run R18 — 2026-09-20 — Slack CLI app creation

- Surface: workspace implementation, mocked creation/installation, and the real
  installed Slack CLI v4.7.0 reading a local manifest with an empty CLI config.
- F01 creation path now runs `slack app install --team <selected-team>
  --environment deployed` after explicit approval and scope display. It uses
  Tag's private project and reads the resulting App ID from Slack CLI metadata.
  No browser creation URL or manual App ID entry is used for this new-app path.
- F04/F07 automated: creation checkpoints survive interruption, pending approval
  does not count as installed, and continuation targets the same App ID. A failed
  creation without recoverable identity blocks automatic duplicate creation.
- F02/L01 regression: existing app linking and settings checks are retained;
  custom hooks, conflicting app links and unrelated settings are preserved.
- Verification: `TAG_TEST_SLACK_MANIFEST=1 ./scripts/ci_check.sh` passed,
  **160 tests**. The real local hook test confirms CLI compatibility only. App
  creation, installation-status parsing against a live response, administrator
  approval and first reply still need live acceptance.
- An initial local-hook test failed because its empty config directory did not
  exist. Fixed the test fixture; the actual CLI hook then passed.
- Bot and Socket Mode credentials still use hidden entry and separate checks;
  the install command does not return its child-process environment to Tag.
- No real app creation/install, permission changes, indexing, messages or
  service restarts occurred. Working installation and configuration were kept.
- Implementation references: [Slack CLI install support for remote manifests](https://docs.slack.dev/changelog/2025/08/14/slack-cli/),
  [manifest hook contract](https://docs.slack.dev/tools/slack-cli/reference/hooks/),
  and [v4.7.0 install implementation](https://github.com/slackapi/slack-cli/blob/v4.7.0/internal/pkg/apps/install.go).

## Run R19 — 2026-09-20 — temporary Python hook path regression

- User live setup: F01 **Fail** before creation approval. Tag rejected its own
  saved manifest hook as custom after uv's Python path changed between runs.
- A deterministic regression reproduced the exact error by preparing the same
  project twice with different Python executable paths. It failed before the fix.
- Fix: recognize the exact Tag manifest helper independently of its Python path,
  refresh the generated command, and resolve temporary interpreter symlinks.
  Custom helpers, additional arguments/commands and extra hooks remain protected.
- Automated regression and real local CLI manifest test passed. Read-only check
  confirmed the user's saved hook is now recognized; no reset was needed.
- Full verification: `TAG_TEST_SLACK_MANIFEST=1 ./scripts/ci_check.sh` passed,
  **162 tests**. F01 live retry: **Needs retest**. No real app creation,
  permission changes, indexing, messages or service changes occurred.

## Run R20 — 2026-09-20 — wrapped Slack app metadata recovery

- User live transcript: Slack CLI reported app `A0C2WTJ5QMR` installed in
  `T0BND7V5J2W`. F01 creation/install portion: user-observed success. Tag's
  subsequent identity recovery: **Fail**; connection and reply remain unverified.
- Read-only inspection found the identity saved under `apps` in `.slack/apps.json`.
  Tag incorrectly assumed the flat team map used by `.slack/apps.dev.json`.
- Regression reproduced failed resume with the actual wrapped schema before
  the fix. Creation fixtures now use that schema. Creation recovery and existing
  link checks share one parser, preserving flat and legacy dev records and
  rejecting malformed wrappers or conflicting identities.
- Targeted tests pass; read-only checks of the actual saved metadata recover
  `A0C2WTJ5QMR` in both paths. Full gate passed: **165 tests**, including the real
  local manifest hook. The mocked resume test issues no create/link commands.
- No Slack mutation, credential entry, service restart, indexing or message was
  performed by the assistant. Live continuation: **Needs retest**. Rerunning the
  existing test launcher should recover the saved app without another creation.

## Run R21 — 2026-09-20 — approved test app deletion and reset

- User requested deletion and a fresh onboarding attempt. Exact target resolved
  from isolated project metadata: `A0C2WTJ5QMR`, workspace `T0BND7V5J2W`.
- Ran Slack CLI app delete with explicit app/workspace flags and confirmed its
  exact-target prompt. Exit 0; CLI reported app uninstalled, manifest deleted,
  and no apps remaining in this project. Remote deletion is permanent.
- Archived the previous isolated test home under
  `.context/onboarding-archive.w5mVyk/previous-test`; local files remain recoverable.
  Recreated the launcher's test home with only Tag Test and Codex defaults.
- Verified no workspace, app, channel or credential selections remain. Slack CLI
  authorization was retained. Working app/installation was not targeted; no
  service restart, indexing or message was performed.
- Automatic credential handoff remains unimplemented. A fresh attempt would
  still reach hidden token prompts; reset does not resolve that implementation gap.

## Run R22 — 2026-09-20 — automatic credential handoff implementation

- User reported app `A0C316GRACE` linked and compatible, but setup still required
  manual Socket Mode credentials. F01 automatic connection: implementation gap.
- Added an explicit automatic-connect action using Slack CLI's documented custom
  deploy hook, in a temporary project targeting only the saved app and workspace.
  Remote app settings remain the source of truth. This action repeats installation
  checks and requires operator approval; pending approval cannot count as success.
- The helper only receives credentials: no bot startup, hosted deployment,
  indexing, or messages. Credential output is not printed. Temporary files are
  owner-only and cleaned on exit. Per ADR 0001, this is not hardened isolation.
- Bot and Socket Mode validation must both pass before the pair is saved
  atomically. Existing configuration and separate history credentials are retained.
  Manual entry is an explicit fallback; interrupted manual progress remains saved.
- Verification: `TAG_TEST_SLACK_MANIFEST=1 ./scripts/ci_check.sh` passed,
  **174 tests**, including nine new handoff tests. The helper-process test used
  synthetic credentials; CLI installation and credential delivery were mocked.
- Live handoff: **Needs retest**. No assistant live install, permission change,
  service restart, indexing, or message occurred. Working app `A0BNAJPP4ER` was
  untouched. Neither a live Slack connection nor a successful reply is established
  by these tests.
- References: [Slack deploy hook contract](https://docs.slack.dev/tools/slack-cli/reference/hooks/#deploy),
  [CLI deploy implementation](https://github.com/slackapi/slack-cli/blob/v4.7.0/cmd/platform/deploy.go),
  [CLI install implementation](https://github.com/slackapi/slack-cli/blob/v4.7.0/internal/pkg/apps/install.go).

## Run R23 — 2026-09-20 — detect the onboarding operator

- User live setup reached a manual member-ID prompt despite an authorized Slack
  CLI account. Updated the access step to read `slack auth list` and offer the
  selected workspace's member IDs as selectable defaults. No credential files
  are read and the bot identity is never used as the operator identity.
- Selecting an account explicitly approves that member's access. Multiple
  accounts remain separate choices. Manual entry and save/exit are available;
  failed, missing, or unmatched authorization falls back to manual entry.
  Existing valid access lists are preserved without a CLI lookup.
- Full gate passed: **179 tests**, including five new tests covering scoped
  parsing, saved policy, automatic selection, manual/exit, and lookup failures.
  CLI account responses were mocked; live UI continuation **Needs retest**.
- No live access list, app permissions, services, indexing, or messages changed.

## Run R24 — 2026-09-20 — recover from no joined channels

- User observed successful account selection, followed by a fatal empty-membership
  channel error. This establishes the account picker worked in their live flow,
  not channel selection, indexing, service readiness, or a successful reply.
- A deterministic picker regression reproduced the exact `No joined Slack
  channels are visible to the bot` error before the fix.
- Interactive selection now explains inviting the connected app in Slack and
  offers Check again or Save and exit. Retry fetches fresh membership and shows
  only joined channels for explicit selection. No automatic joining or indexing.
- Full gate passed: **181 tests**, including refresh-after-invitation and safe
  pause tests with mocked channel lists. Live continuation **Needs retest**.
- No live Slack mutation, service restart, indexing, or test message performed.

## Run R25 — 2026-09-20 — select channels before joining

- User rejected R24's invite-first flow. The interactive picker now includes
  visible unjoined public channels, alongside joined public/private channels.
  Selection precedes an explicit exact-channel join approval. Private channels
  the bot has not joined are not offered as automatically joinable.
- Approved joins use POST `conversations.join` only for selected nonmember
  channels. The returned channel ID and membership must match before proceeding.
  Back/exit perform no joins; existing members require no join calls. Missing
  scope recovery explains `channels:join` and retains the in-progress selection
  for retry, including after manual invitation.
- Added `channels:join` to the new-app template and manifest check. No existing
  remote app settings or credentials were changed. Earlier apps may need an
  operator-approved scope addition and reinstall before automatic joining works.
- Full gate passed before the manifest-check/docs follow-up: **186 tests**.
  New synthetic tests cover unjoined channel discovery, exact selected joins,
  approval/exit, missing-scope recovery, unconfirmed results, and POST encoding.
  Live selection/join/indexing/reply remain **Needs retest**. No assistant live
  joins, scope changes, service restarts, indexing, or messages occurred.
- Reference: [Slack channels:join](https://docs.slack.dev/reference/scopes/channels.join)
  and [conversations.join](https://docs.slack.dev/reference/methods/conversations.join/).

## Run R26 — 2026-09-20 — private-only workspace and onboarding relaunch

- Added explicit empty-list guidance for private-only workspaces: a channel
  member invites the app, then Check again refreshes the list. If no suitable
  channel exists, setup suggests creating one in Slack or contacting an admin;
  Tag does not create channels automatically.
- Private-only lists show joined private channels without public-join wording
  or join API calls. Two regression tests cover immediate private selection
  and empty-list recovery after a private invitation. Full gate: **188 tests**.
- At the user's request, reopened `.context/test-onboarding.command` in Terminal.
  Launch succeeded; saved setup was not reset and `--no-start` remains enabled.
  Conductor's local-environment check confirmed this runs on the user's Mac.
- No assistant channel selection, app permission changes, joining, indexing,
  messages, or service restart. Live private-only acceptance remains unverified.

## Run R27 — 2026-09-20 — invitation-following memory policy

- User explicitly approved automatic memory for every channel Tag is invited to
  later, superseding the original selected-only future indexing rule.
- Added `SLACK_CHANNEL_POLICY=invited`. New incomplete onboarding discloses
  indexing of all joined channels, including future invitations, before setup
  approval. Existing completed configurations keep selected policy unless opted
  in through updated Settings/config. No live user settings were changed here.
- The bridge worker reconciles bot membership about every minute and requests
  incremental MFS sync on changes or about every five minutes. It never joins
  channels. History credentials are independently validated, errors are sanitized
  and retried without interactive prompts, and empty membership never submits an
  empty connector filter. Failed checks revoke normal live channel access.
- Fresh membership scopes replace the workspace's managed scopes; unrelated
  saved configuration and credentials remain intact. Current-channel backend
  scope narrowing is retained. Removal revokes normal access after reconciliation,
  not previously stored records or already running indexing jobs. ADR 0001 applies.
- App Home shows the invitation policy and ignores stale manual-picker actions.
  Inspect/status report invitation-memory state separately from service health;
  `sync_requested` is not an observed completed index or successful reply.
- Full CI gate: **200 tests**. Synthetic coverage includes new public/private
  membership, no unjoined-channel indexing, duplicate discovery, removal, empty
  membership, history/membership/index failures, concurrent settings changes,
  stop handling, legacy setup preservation, current-channel scoping, and App Home.
- I01 live invitation→index→reply acceptance: **Needs retest**. No live indexing,
  joins, app permission changes, messages, or service restarts performed.

## Run R28 — 2026-09-20 — join-scope repair and onboarding restart

- User live transcript: joining `#general` failed with `missing_scope` for
  `channels:join`. Read-only CLI remote-manifest inspection of saved app
  `A0C316GRACE` in `T0BND7V5J2W` confirmed the scope is absent. No credential
  values or manifest contents were printed.
- Added an approved scope-repair callback in channel selection: save pending
  channel IDs, name the exact app/workspace and `channels:join` change, then use
  CLI to reinstall and receive refreshed credentials privately. No template
  replacement; remote settings are copied and only the missing scope appended.
  A changed second remote snapshot aborts before reinstall. No atomic Slack-side
  concurrency guarantee is claimed; normal refresh remains remote-source/no-force.
- Tests cover exact selected-channel retry with the refreshed token, unchanged
  settings, already-present scope refresh, detected manifest races, declining
  permission approval, and preserving separate history credentials. Full gate:
  **206 tests**. Scope update/credential delivery were mocked, not live results.
- Restart requested by user: verified two old onboarding process identities,
  workspace paths and isolated Tag home; interrupted both, then terminated the
  one still waiting. Reopened the existing `--no-start` launcher in Terminal.
  Saved configuration was retained. Production Tag services were not targeted.
- Live repair/join retry: **Needs retest**. The assistant did not change Slack
  permissions, reinstall the app, join a channel, index history, or send messages.
- Implementation reference: [Slack CLI 4.7 install/update behavior](https://github.com/slackapi/slack-cli/blob/v4.7.0/internal/pkg/apps/install.go).

### R29 — 2026-09-20 — manual permission recovery

- Supersedes R28's automatic permission-repair implementation at user request.
  Removed scope-update/reinstall callbacks and their temporary pending-selection
  configuration. Normal approved credential handoff remains remote-source/no-force.
- Missing-permission errors explain the scope, blocked operation, and manual
  Slack settings/reinstallation steps. Socket Mode and history credentials have
  distinct guidance. Opening settings does not itself retry the failed operation.
- Automated checks: **202 tests passed**, including manual guidance for three
  credential cases and opening settings after a failed channel join. These are
  mocked checks, not live integration results.
- Live acceptance: **Needs retest**. No Slack permissions, installations, services,
  indexing, or messages were changed during this revision. An already-running
  setup process must be reopened to load the updated code.

### R30 — 2026-09-20 — automatic first connection and actionable failures

- User-observed real onboarding failure: credential handoff returned a generic
  error and repeated the initial connection menu. Read-only inspection of recent
  local Slack CLI logs found `service_limits_exceeded` during
  `apps.developerInstall`. Which service limit was reached remains unknown.
  No raw logs or credentials were copied into this record.
- Connection now attempts the handoff once by default. Failure pauses on a
  separate recovery menu; opening app settings does not repeat installation.
  Known errors receive fixed explanations rather than raw subprocess output.
  No automatic permission repair was introduced.
- Regression tests first failed for the initial menu, hidden service-limit code,
  and recovery behavior, then passed after the changes. Full CI: **205 tests passed**.
- Live automatic handoff: **Blocked / needs retest**. No live installation was
  retried, no limits resolved, and no Slack settings, services, indexing, or test
  messages changed in this revision. Mocked success is not a live result.

### R31 — 2026-09-20 — readable connection failure screen

- User screenshot showed dense connection prose, mid-word terminal wrapping,
  and competing menu emphasis. Replaced the paragraph with a short heading,
  word-wrapped next action, separate error code, and saved-progress note.
- Keyboard help now occupies its own line. Single-choice defaults are neutral
  after the pointer moves; multi-channel checkbox styling remains unchanged.
- Full CI: **206 tests passed**. Added a 48-column wrapping check; existing
  real-PTY keyboard, cancellation, default, and terminal-restoration tests pass.
  Manually exercised both numbered and Questionary error-menu fixtures at a
  68-column width. Fixtures made no Slack calls and are not integration results.
- Slack service-limit resolution and successful live connection remain unverified.

### R32 — 2026-09-20 — confirmed free-workspace app-limit blocker

- Corrects R30/R31's incomplete diagnosis: the CLI's rendered error explicitly
  identifies the free-workspace **10-app installation limit**. The API's bare
  error code alone did not specify it. Failed refresh requests target the same
  app, not a newly created app on every retry.
- User approved one controlled credential attempt for current test app
  `A0C3XKZ0X4Y` in `T0BND7V5J2W`. It failed with
  `service_limits_exceeded`; no credentials were saved, services started,
  messages sent, or history indexed. No automatic retry was performed.
- Read-only CLI checks report both the current app and older test app
  `A0C316GRACE` installed. Removing another app requires separate approval;
  the working app `A0BNAJPP4ER` remains out of scope.
- Added a regression test using the CLI's specific limit wording. It failed
  before the change and passed afterward: setup now tells the operator to
  uninstall an unused app (or ask an admin), with a paid plan as an alternative.
  Generic limit errors still avoid guessing which limit applies.
- Live connection: **Blocked by Slack app limit**. No claim of successful
  connection, readiness, or reply. Reference:
  [Slack workspace limits](https://slack.com/help/articles/115002422943-Usage-limits-for-free-workspaces).

## Your manual review

Open `.context/setup-workflow.html` and start with F01. Reload between independent
cases; do not reload inside pause/resume or replay tests. For each result append:

| Run / date | Scenario | Tester | Artifact | Result | Actual behavior / evidence | Retest of |
| --- | --- | --- | --- | --- | --- | --- |
| Pending | F01 | User | Record hash | Not tested | | |

Example feedback: `F02 Fail — TeamHelper changed to OpenMax on the channel screen`.
Keep screenshots and logs free of credentials and private Slack content.
