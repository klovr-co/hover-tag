# ADR 0012: One Slack app, with Tags as profiles

- Status: Proposed
- Date: 2026-10-15
- Issue: [#232](https://github.com/klovr-co/hover-tag/issues/232), PR [#238](https://github.com/klovr-co/hover-tag/pull/238)
- Related: [ADR 0001](0001-credential-boundary.md), [ADR 0005](0005-multiple-tag-instances.md),
  [ADR 0009](0009-thread-scoped-agent-conversations.md), [ADR 0011](0011-chief-of-tag.md),
  [handoffs](../../references/handoffs.md)
- Changes ADR 0011: Chief-of-Tag creates profiles, not Slack apps.

## Context

ADR 0011 gives every Tag its own Slack app. Creating a Tag from Slack then
needs a Slack CLI sign-in by a service account, often an admin's install
approval, and a slot under Slack's 10-app cap on the free plan. Each of
those steps needs a person outside the thread.

Most Tags don't need their own Slack identity. They need their own name,
picture, instructions, working folder, model and memory. Slack's
`chat:write.customize` scope lets one app post each message with its own
`username` and `icon_url`. The message still shows as coming from the app
(Slack labels it APP), and nobody can @mention that name.

## Decision

**One Slack app, "Tag", belongs to the Chief-of-Tag instance.** Chief-of-Tag
is the app's default profile. Only Chief-of-Tag's instance holds the app's
`xapp-` and `xoxb-` tokens and runs the Socket Mode connection.

**A profile is a Tag instance without its own Slack app.** It keeps everything
in its instance home except Slack tokens: name, picture, description,
working folder, backend settings, routines, memory, agent sessions and backend
credentials. `tag list` shows `kind: "profile"`. Its roster card names
Chief-of-Tag's app and has no bot member ID.

**People name the profile in the message.**

- `@Tag designer: make a banner` and `@Tag ask Ops to check the deploy` go
  straight to that profile. The name must come first and be followed by `:`
  or `,`, or come after `ask`. The match is exact and ignores case. A name
  found anywhere else in the text doesn't count, so "ask the designer
  whether…" is not a call.
- With no name, Chief-of-Tag picks one owner as in ADR 0011: route, never split or
  combine, and at most two hops.
- DMs to the app work the same way.
- Profile names must be unique in the installation and can't be `chief-of-tag`, `tag`
  or a reserved command. Tag refuses a name that matches a workspace member's
  display name, because Slack's policy forbids impersonating users.

**Replies carry the profile's name and picture.** Every message a profile
posts uses `username` and `icon_url`: `chat.postMessage`, `chat.startStream`,
`chat.postEphemeral`, and file uploads if `files.completeUploadExternal` takes them (the scope page lists it; not checked). Slack's `chat.update` takes no
identity fields, so edits rely on Slack keeping the original ones. This has
not been checked live. Reactions and the assistant "is thinking" status show
the app, not the profile.

- `icon_url` must be a public HTTPS URL. A profile's picture is one of the
  built-in characters, linked by a `raw.githubusercontent.com` URL pinned to
  a commit of this public repository. A person may instead set any public
  HTTPS URL with `tag NAME profile icon-url URL`. Tag doesn't host uploaded
  pictures in #238; that is a follow-up under
  [#206](https://github.com/klovr-co/hover-tag/issues/206). Without a valid
  URL, the profile posts with the app's icon and its own name.

**Threads stay with their owner profile.**

- The first profile to answer in a thread owns it. The owner is saved in
  Chief-of-Tag's `state/thread-owners/<team>-<channel>-<thread_ts>.json`, written with
  `scope_lock`.
- A later `@Tag` in that thread without a name goes to the owner. Naming a
  different profile hands the thread to that profile, and the bridge posts
  one line saying so.
- Each profile keeps its own agent sessions (ADR 0009), keyed by thread. A
  hand-over starts a fresh session for the new owner, which reads the thread
  from Slack.
- All profiles share one bot user, so "Tag's own replies" can't be told
  apart by `bot_id` any more. The bridge records the `ts` of every message
  it posts with the profile that posted it (`state/profile-messages/`). When
  it builds thread context, it labels earlier replies by profile and leaves
  out only the current owner's own replies (ADR 0009). Slack message metadata
  could carry the same information, but it needs schemas in the manifest, so
  the local record is the source of truth.

**Handoffs (#177) and routing between profiles run in-process.**

- A route or escalation between two profiles doesn't go through Slack.
  Chief-of-Tag's bridge posts the same visible marker line as today, as the sending
  profile, and starts the owner's run directly. This removes the unverified
  dependency on Slack delivering `app_mention` for a bot's own mention.
- Handoff and route records gain `from_profile` and `to_profile`. Status
  lines, results and activity entries name the profile, not the app.
- The hop limit and `OPENTAG_HANDOFF_DEPTH` are unchanged.
- An owner still accepts a route only from Chief-of-Tag, checked against the profile
  ID, not the Slack user.

**Approvals (#208) are attributed to the profile.**

- Backend approval prompts are ephemeral, sent only to the requester, posted
  with the profile's name and picture, in the requester's thread.
- Every block action reaches the one app, so each action value carries the
  profile ID and run key. The bridge resolves the approval against that
  profile's run and checks that the person clicking is the requester, as
  today.
- Saved approval rules (Codex prefix or host rules) belong to the profile's
  backend home and never apply to another profile.

**Creating a Tag means creating a profile.**

- Chief-of-Tag proposes a profile in the thread with the same checks as ADR 0011:
  a real boundary, unique name, one open proposal, and 60-minute expiry.
  The requester approves or rejects it.
- On approval, Tag creates the instance with
  `tag add --profile --preset FILE`, with no Slack app step, starts it and
  saves starting routines. No admin approval, service account or app
  configuration token is involved, and profiles don't count against the
  app cap. `OPENTAG_CHIEF_MAX_TAGS` stays the safety cap (default 10).
- **Creating app Tags is on hold.** Installations run one Slack app, so
  Chief-of-Tag never creates a Tag with its own Slack app, and nobody needs
  the service account while there is one app. ADR 0011's service-account,
  Slack CLI and `apps.manifest.create` code stays in the branch unchanged,
  but nothing calls it from Chief-of-Tag's flow and it isn't extended.
  Resuming it needs a new decision.

**Mixed mode, no automatic migration.**

- Existing Tags with their own app keep working unchanged. Chief-of-Tag routes to them
  through Slack, as in ADR 0011.
- Nothing is converted, renamed or reinstalled automatically. To turn on
  profiles, a person runs `tag chief-of-tag profiles enable`. It adds
  `chat:write.customize` to Chief-of-Tag's manifest. Slack then asks for a reinstall
  to grant the new scope, which a person does. Until then, Chief-of-Tag proposes
  nothing.
- A person can convert an app Tag to a profile later with
  `tag NAME profile convert`, which leaves the old Slack app in place
  (AGENTS.md, destructive operations).

## Trade-offs

- **No @mention per profile.** People type the name instead. Slack
  autocomplete doesn't help, and a typo goes to Chief-of-Tag, which routes it. A
  profile can't be paged with a Slack notification by mention, and other apps
  can't trigger one.
- **Slack actions are not a per-profile boundary.** Chief-of-Tag's bot token is the
  only token, and ADR 0001 already says backends inherit it. Any profile's
  backend can post as any name, read any channel the app is in, or call any
  method the token allows. Per-profile channel lists, allowed users and name
  checks are guardrails in the bridge, not boundaries. A Tag that needs its
  own Slack scopes, channels or audit identity must be an app Tag. Backend
  credentials, working folders and memory stay separate per profile, as
  before.
- **Slack shows one app.** Slack's audit logs, analytics, app settings and
  channel membership count one app. Every profile can post in every channel
  Chief-of-Tag is in. A profile's channel list limits only where it answers.
- **One connection and one process.** Chief-of-Tag's bridge serves every profile, so
  restarting or stopping it stops them all. `tag NAME stop` on a profile
  turns off its runs; it doesn't stop a process. Runs keep their own backend
  process per request, so concurrency limits stay per profile.
- **Pictures need a public URL.** That makes a custom picture visible to
  anyone with the URL.
- **Edited messages are unverified.** If `chat.update` drops the custom name,
  streamed replies that are later edited show the app name instead.

## Implementation plan for #238

1. **Profiles.** Add `kind` (`app` or `profile`) to the instance record,
   `tag add --profile`, and roster cards for profiles. `tag list` and
   Tag.app show the kind. Add tests that a profile has no Slack tokens and
   that its credentials stay in its own home.
2. **Profile-aware posting.** Add a `ProfileIdentity` (name, icon URL) that
   every post helper accepts, and the `profile-messages` record. Move
   `bot_id` own-message checks to that record.
3. **Message dispatch.** Add the name parser, `thread-owners`, and dispatch in
   Chief-of-Tag's bridge: named profile, owner of the thread, else Chief-of-Tag. Run each
   profile's backend with that profile's home, settings and sessions.
4. **In-process routing and handoffs.** Teach `route` and `ask` between
   profiles to start runs directly. Add profile IDs to the records and
   attribute status, activity and approvals.
5. **Creation.** Point Chief-of-Tag's proposal flow at `tag add --profile`.
   Unwire the service-account, Slack CLI and `apps.manifest.create` paths
   from the approval flow, leaving their code in place (on hold, above).
6. **Enable and convert.** `tag chief-of-tag profiles enable` (manifest scope plus
   reinstall step for a person) and `tag NAME profile convert`.
7. **Docs and Tag.app.** Update ADR 0011's status, the handoffs reference,
   CLI and app-protocol docs, CHANGELOG, and the Details tab's profile view
   (name, picture URL, kind).

Steps 1–4 are the core and get the test for "a profile cannot create Tags".
No step creates, reinstalls or deletes a Slack app in tests. Live checks
(edit keeps the name, streaming with a custom name, ephemeral with a custom
name) wait for an explicit go-ahead.

## Follow-ups

- Hosting uploaded profile pictures at a public URL
  ([#206](https://github.com/klovr-co/hover-tag/issues/206)).
- Resuming app-Tag creation by Chief-of-Tag, if an installation ever needs a
  second Slack app.
