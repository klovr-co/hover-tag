# ADR 0012: One Slack app, with Tags as profiles

- Status: Proposed
- Date: 2026-10-15
- Issue: [#232](https://github.com/klovr-co/hover-tag/issues/232), PR [#238](https://github.com/klovr-co/hover-tag/pull/238)
- Related: [ADR 0001](0001-credential-boundary.md), [ADR 0005](0005-multiple-tag-instances.md),
  [ADR 0009](0009-thread-scoped-agent-conversations.md), [ADR 0011](0011-lead-tag.md),
  [handoffs](../../references/handoffs.md)
- Changes ADR 0011: Lead creates profiles, not Slack apps.

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

**One Slack app, "Tag", belongs to the Lead instance.** Lead (called Chief of
Staff in #232) is the app's default profile. Only Lead's instance holds the
app's `xapp-` and `xoxb-` tokens and runs the Socket Mode connection.

**A profile is a Tag instance without its own Slack app.** It keeps everything
in its instance home except Slack tokens: name, picture, description,
working folder, backend settings, routines, memory, agent sessions and backend
credentials. `tag list` shows `kind: "profile"`. Its roster card names
Lead's app and has no bot member ID.

**People name the profile in the message.**

- `@Tag designer: make a banner` and `@Tag ask Ops to check the deploy` go
  straight to that profile. The name must come first and be followed by `:`
  or `,`, or come after `ask`. The match is exact and ignores case. A name
  found anywhere else in the text doesn't count, so "ask the designer
  whether…" is not a call.
- With no name, Lead picks one owner as in ADR 0011: route, never split or
  combine, and at most two hops.
- DMs to the app work the same way.
- Profile names must be unique in the installation and can't be `lead`, `tag`
  or a reserved command. Tag refuses a name that matches a workspace member's
  display name, because Slack's policy forbids impersonating users.

**Replies carry the profile's name and picture.** Every message a profile
posts uses `username` and `icon_url`: `chat.postMessage`, `chat.startStream`,
`chat.postEphemeral`, and file uploads if `files.completeUploadExternal` takes them (the scope page lists it; not checked). Slack's `chat.update` takes no
identity fields, so edits rely on Slack keeping the original ones. This has
not been checked live. Reactions and the assistant "is thinking" status show
the app, not the profile.

- `icon_url` must be a public HTTPS URL. Built-in characters use pinned
  `raw.githubusercontent.com` URLs from this public repository. A custom
  picture needs a public URL set with `tag NAME profile icon-url URL`.
  Without one, the profile posts with the app's icon and its own name.

**Threads stay with their owner profile.**

- The first profile to answer in a thread owns it. The owner is saved in
  Lead's `state/thread-owners/<team>-<channel>-<thread_ts>.json`, written with
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
  Lead's bridge posts the same visible marker line as today, as the sending
  profile, and starts the owner's run directly. This removes the unverified
  dependency on Slack delivering `app_mention` for a bot's own mention.
- Handoff and route records gain `from_profile` and `to_profile`. Status
  lines, results and activity entries name the profile, not the app.
- The hop limit and `OPENTAG_HANDOFF_DEPTH` are unchanged.
- An owner still accepts a route only from Lead, checked against the profile
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

- Lead proposes a profile in the thread with the same checks as ADR 0011:
  a real boundary, unique name, one open proposal, and 60-minute expiry.
  The requester approves or rejects it.
- On approval, Tag creates the instance with
  `tag add --profile --preset FILE`, with no Slack app step, starts it and
  saves starting routines. No admin approval, service account or app
  configuration token is involved, and profiles don't count against the
  app cap. `OPENTAG_LEAD_MAX_TAGS` stays the safety cap (default 10).
- Only a person creates a Tag with its own Slack app, in Tag.app or with
  `tag add`, when it needs a separate Slack identity or Slack permissions.

**Mixed mode, no automatic migration.**

- Existing Tags with their own app keep working unchanged. Lead routes to them
  through Slack, as in ADR 0011.
- Nothing is converted, renamed or reinstalled automatically. To turn on
  profiles, a person runs `tag lead profiles enable`. It adds
  `chat:write.customize` to Lead's manifest. Slack then asks for a reinstall
  to grant the new scope, which a person does. Until then, Lead proposes
  nothing.
- A person can convert an app Tag to a profile later with
  `tag NAME profile convert`, which leaves the old Slack app in place
  (AGENTS.md, destructive operations).

## Trade-offs

- **No @mention per profile.** People type the name instead. Slack
  autocomplete doesn't help, and a typo goes to Lead, which routes it. A
  profile can't be paged with a Slack notification by mention, and other apps
  can't trigger one.
- **Slack actions are not a per-profile boundary.** Lead's bot token is the
  only token, and ADR 0001 already says backends inherit it. Any profile's
  backend can post as any name, read any channel the app is in, or call any
  method the token allows. Per-profile channel lists, allowed users and name
  checks are guardrails in the bridge, not boundaries. A Tag that needs its
  own Slack scopes, channels or audit identity must be an app Tag. Backend
  credentials, working folders and memory stay separate per profile, as
  before.
- **Slack shows one app.** Slack's audit logs, analytics, app settings and
  channel membership count one app. Every profile can post in every channel
  Lead is in. A profile's channel list limits only where it answers.
- **One connection and one process.** Lead's bridge serves every profile, so
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
   Lead's bridge: named profile, owner of the thread, else Lead. Run each
   profile's backend with that profile's home, settings and sessions.
4. **In-process routing and handoffs.** Teach `route` and `ask` between
   profiles to start runs directly. Add profile IDs to the records and
   attribute status, activity and approvals.
5. **Creation.** Point Lead's proposal flow at `tag add --profile`. Take the
   service-account, Slack CLI and `apps.manifest.create` paths out of the
   approval flow. Either drop that code from #238 or keep it behind
   `tag add` for app Tags (open question below).
6. **Enable and convert.** `tag lead profiles enable` (manifest scope plus
   reinstall step for a person) and `tag NAME profile convert`.
7. **Docs and Tag.app.** Update ADR 0011's status, the handoffs reference,
   CLI and app-protocol docs, CHANGELOG, and the Details tab's profile view
   (name, picture URL, kind).

Steps 1–4 are the core and get the test for "a profile cannot create Tags".
No step creates, reinstalls or deletes a Slack app in tests. Live checks
(edit keeps the name, streaming with a custom name, ephemeral with a custom
name) wait for an explicit go-ahead.

## Open questions

- Should #238 delete the service-account and `apps.manifest.create` code, or
  keep it for creating app Tags from Tag.app?
- Should a custom picture upload go to a Tag-hosted public URL, or stay
  URL-only?
- Name: the code calls the role Lead; #232 and this change say Chief of
  Staff.
