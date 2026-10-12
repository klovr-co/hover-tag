# ADR 0010: Chief of Staff routes each request to one owner and alone creates Tags

- Status: Accepted
- Date: 2026-10-13
- Issue: [#232](https://github.com/klovr-co/hover-tag/issues/232)
- Related: [ADR 0005](0005-multiple-tag-instances.md), [handoffs](../../references/handoffs.md)

## Context

An installation can run many Tags, and people need one place to send work
when they don't know which Tag owns it. Tags also need to be created without
manual terminal steps, but creating a Slack app is outward-facing and needs a
person's approval.

Research on multi-agent systems found that splitting one request across agents
and then merging or rewording their outputs causes most errors, costs 3–15×
the tokens, and loses context. Agents work best as separate lanes divided by a
real context boundary, with full context passed to a single owner (see the
evidence in the [#232 design comment](https://github.com/klovr-co/hover-tag/issues/232#issuecomment-6128137017)).

## Decision

**One Tag per installation is Chief of Staff, a dispatcher, not an orchestrator.**

- `<TAG_HOME>/state/chief-of-staff.json` names one Tag, so only one Tag can
  hold the role. `tag NAME chief-of-staff set` and Tag.app's Details move it.
  Every bridge reads the file on each check, so a move takes effect without a
  restart. Renaming a Tag (`tag_rename`) carries the role with it.
- Migration `chief-of-staff-v1` runs on `tag start`, `setup` and `list`, with
  no TTY. If no existing Tag holds the role, it gives it to the main Tag, else
  the first Tag with a Slack app, else the first Tag. It never renames a Slack
  app or changes settings. Completion is recorded only after the saved role
  reads back. Each run checks again, so a role left on a removed Tag is
  repaired.
- **Routing.** Chief of Staff's agent runs `tag_handoff.py route --to NAME
  --reason TEXT`. After the reply, the bridge posts one message in the same
  thread: `<@Owner> reason` and `_Routed r-… for <@requester>. You own this
  request._`. The owner's bridge accepts that marker only from the designated
  Chief of Staff. It runs as the requester, with the whole thread as context,
  and replies there itself. Its approvals stay in the requester's thread.
  Nothing is combined or reworded. A direct @mention of a specialist skips
  Chief of Staff completely.
- **Escalation.** With a Chief of Staff, `ask` is escalation only: a
  specialist may ask Chief of Staff, and only Chief of Staff, one judgment
  question. It still writes the answer itself. Chief of Staff never uses `ask`.
- **Hop limit.** A request makes at most two Tag-to-Tag hops: route, then one
  escalation. A Tag answering another Tag can neither route nor ask
  (`OPENTAG_HANDOFF_DEPTH=1`). One Tag action per reply.
- **Flat roster.** Each bridge publishes a public card
  (`state/tag-roster/<id>.json`: name, description, bot member ID, team,
  channels, model). It contains no tokens or private settings. Chief of Staff's
  peers are every card in its team, and a specialist's peer is Chief of Staff,
  so no `OPENTAG_PEER_TAGS` entry is needed. There is no deeper tree.
- **Decisions use the Tag's own backend.** A decision classifier can plug in
  later as a routing hint, without changing the bridge.

**Creating a Tag is the last resort, and a person approves it.**

- Chief of Staff's agent runs `tag_chief.py propose`. The proposal must name
  a real boundary and say why a skill or an in-process subagent won't do. The
  boundaries are credentials, memory, parallel, identity and owner.
- The bridge checks the role again, then the limits:
  - at most `OPENTAG_CHIEF_MAX_TAGS` Tags (default 10, Slack's free-plan
    app cap);
  - one proposal open at a time;
  - a unique name;
  - only channels Chief of Staff is in.

  It saves `state/tag-proposals/p-….json` and posts an Approve/Reject card in
  the requester's thread.
- Only the requester, who must be an authorized user, can decide. Proposals
  expire after 60 minutes. **Nothing reaches Slack before approval**, so a
  rejected or expired proposal creates nothing.
- After approval, `tag_creator` drives `tag add --json --preset FILE` exactly
  as Tag.app does:
  - The preset sets the name, description, model, owners (Chief of Staff's)
    and the Slack CLI folder.
  - `add` first emits `{"type":"tag","tag":ID}`, so a paused setup can resume.
  - The driver answers `profile`, `default_model`, `workspace`,
    `approve_setup` and `channels` from the proposal. It pauses on anything
    only a person can do: `app_install` (admin approval), `slack_login`,
    `ai_connection`, `credentials_next` or an unknown question.
  - Chief of Staff posts that one step with a **Check again** button, which
    resumes `tag ID setup --json`. The `tag-create.json` checkpoint prevents a
    second app.
- Once setup completes, the driver records the Tag as created by Chief of
  Staff, runs `tag autostart keep ID` and `tag ID start`, and reports in the
  thread.
- **Routines.** Starting routines go to `<Tag home>/state/pending-routines.json`
  (schema 1: `routines` of `schedule` and `prompt`). The routines feature
  imports and removes this file.
- A Tag that is not Chief of Staff cannot create Tags. Three checks enforce
  this:
  - the agent helper refuses;
  - the bridge refuses, and so does the creator;
  - `tag add`, `setup`, `reset`, `remove`, `abandon` and `chief-of-staff`
    refuse to run inside a Tag's request (`OPENTAG_CALLER_ID` or
    `OPENTAG_HANDOFF_DEPTH` is set).

**Slack apps belong to a service account, in its own Slack CLI folder.**

- The Slack CLI creates the app (from the manifest), installs it (the CLI's
  `apps.developerInstall`), and hands over the `xapp-` and `xoxb-` tokens.
  Tag drives the `slack` binary and never calls those endpoints directly.
- `tag chief-of-staff sign-in` signs the service account in to
  `<TAG_HOME>/shared/slack/service-account` (or `OPENTAG_CHIEF_SLACK_CONFIG`)
  with `--config-dir`, and saves the sign-in as `OPENTAG_CHIEF_SLACK_SIGN_IN`.
  The operator's default `~/.slack` sign-in is never touched.
- A created Tag keeps the folder in `OPENTAG_SLACK_CLI_CONFIG`. Tag applies it
  as `TAG_SLACK_CONFIG_DIR` for every Slack CLI command for that Tag, so
  renames, manifest migrations and app deletion use the same account.
- **Fallback:** if the service account isn't signed in but an app
  configuration token is saved (`tag chief-of-staff config-token --stdin`), Tag
  creates the app with `apps.manifest.create`. It rotates the 12-hour token
  with `tooling.tokens.rotate` before use, saving the new single-use refresh
  token first. It then asks a person to open the install link and add the
  app-level token in Tag.app or `tag ID setup`. Tokens are never asked for in
  Slack.
- **Admin approval.** Tag posts the step for an admin and resumes on Check
  again. Slack's approval methods (`admin.apps.requests.list`,
  `admin.apps.approve`) need Enterprise Grid admin scopes, so Tag doesn't call
  them.

## Consequences

- After upgrade, an installation with several Tags has a Chief of Staff, so
  `ask` between specialists is limited to escalation. Combining answers from
  several Tags remains only when no Chief of Staff is known: a bridge started
  without `TAG_ID`, or peers configured across machines.
- Routing works only between Tags of one installation whose bridges have
  published a card. The owner must be in the channel; otherwise Chief of Staff
  asks for an invitation.
- Not yet verified in a live workspace: Slack delivering `app_mention` for a
  message one app's bot posts mentioning another app's bot. Routes depend on
  it, as handoffs do.
- Idle Tags that Chief of Staff created are not flagged for removal yet.
