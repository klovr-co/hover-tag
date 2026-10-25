# ADR 0012: One agent loop; Tags are personas that bundle skills

- Status: Proposed
- Date: 2026-10-25
- Issue: [#232](https://github.com/klovr-co/hover-tag/issues/232), PR [#238](https://github.com/klovr-co/hover-tag/pull/238)
- Related: [ADR 0001](0001-credential-boundary.md), [ADR 0005](0005-multiple-tag-instances.md),
  [ADR 0009](0009-thread-scoped-agent-conversations.md), [ADR 0011](0011-lead-tag.md),
  skills [#176](https://github.com/klovr-co/hover-tag/issues/176),
  routines [#237](https://github.com/klovr-co/hover-tag/pull/237),
  handoffs [#177](https://github.com/klovr-co/hover-tag/pull/177),
  approvals [#208](https://github.com/klovr-co/hover-tag/pull/208)
- Source: Anthropic, [Building agents: When to fold subagents into your main agent loop](https://claude.com/resources/articles/building-agents-when-to-fold-subagents-into-your-main-agent-loop) (22 Oct 2026)
- Changes ADR 0011: new Tags are personas, not instances; creating app Tags stays on hold.
- Replaces two earlier drafts of this ADR: "Tags as profiles" (one
  instance per profile; code on branch `wip/tag-profiles`) and "skills instead
  of profiles" (no named Tags).

## Context

People like having several Tags: "Finance", "Designer", "Ops". A name and a
picture tell them who to ask and who answered. That is a user-facing idea.

ADR 0011 and the first draft of this ADR built each named Tag as its own
agent: its own instance, sessions and memory, with Lead dispatching to it and
an inbox between processes. Anthropic's article on folding subagents into the
main loop explains why that is the wrong default:

- One model with one context, loading skills as it needs them, often beats a
  set of specialist agents on quality, cost and latency.
- Specialist knowledge (procedures, rules, judgment calls) fits in skills, and
  the model picks the skill itself.
- Splitting agents by topic or team copies the org chart (Conway's law), and
  each hand-over loses context, like a game of telephone.
- Hard limits belong inside tools, not in the boundaries between agents.
- Split only for a measured reason, and keep a split only if folding it back
  scores worse.

So this ADR keeps named Tags for people and runs them as one agent.

## Decision

**Under the hood: one Slack app, one agent loop, skills.** Each request runs
in one backend session per thread (ADR 0009), in one Tag instance, with one
context. Specialist knowledge is skills (#176) in that instance's working
folder. Hard limits stay inside tools: backend approvals (#208), the bridge's
checks on helpers, and validation in `tag_*` commands.

**User-facing: Tags are personas.** A persona has:

- a name, unique in the installation, that isn't a workspace member's display
  name (Slack forbids impersonating users);
- a picture: a built-in character by a `raw.githubusercontent.com` URL pinned
  to a commit of this repository, or any public HTTPS URL;
- a short description;
- the skills it bundles;
- optionally a memory scope, a subset of the instance's `MFS_ALLOWED_SCOPES`.

Personas live in the instance's settings. They have no Slack tokens,
sessions, backend credentials or process of their own.

**Lead is the default persona.** It answers when nobody is named, and it is
the router. In practice:

- `@Tag Finance: close the month` or `@Tag ask Finance to check the invoice`
  calls Finance. The name must come first and be followed by `:` or `,`, or
  come after `ask`. The match is exact and ignores case. "ask the finance team
  whether…" is not a call.
- With no name, the same loop picks the persona, as it picks a skill: Lead's
  instructions list the personas and their descriptions, and the agent names
  its choice with a helper call. There is no separate classifier call and no
  second agent.
- A thread remembers its persona, so a later `@Tag` without a name stays with
  it. Naming another persona switches the thread, and the bridge says so in
  one line.
- Switching persona keeps the same session. The model keeps the whole thread
  in context and loads the new persona's skills. Nothing is retold, summarized
  or handed over.
- DMs work the same way.

**Replies carry the persona's name and picture.** The bridge posts with
`chat:write.customize` (`username`, `icon_url`) on `chat.postMessage`,
`chat.startStream` and `chat.postEphemeral`, and on file uploads if Slack
accepts them there. Slack labels these messages APP, and a persona can't be
@mentioned. `chat.update` takes no identity fields, so edits rely on Slack
keeping the original ones; that hasn't been checked live. Reactions and the
"is thinking" status show the app. Without the scope, replies post as the app
and start with the persona's name.

**Thread context tells personas apart.** All personas share one bot user, so
`bot_id` can't tell them apart. The bridge records which persona posted each
message `ts` and labels earlier replies by persona in the thread context.

**What a persona's settings are, and aren't.**

- The bundle shapes the run: the persona's description and skill list go into
  the instructions, so the model reaches for those skills first. All of the
  instance's skills stay installed. A hard per-persona skill subset is added
  only if the evaluation shows the model choosing the wrong one.
- The memory scope is a guardrail. The memory helper limits the run to the
  persona's scopes, but the token is the instance's.
- Neither is a credential boundary. Every persona can do whatever the
  instance's Slack token, backend credentials and working folder allow
  (ADR 0001).

**A separate instance (ADR 0005) only for a real boundary:**

- credentials or owner: production access, a different owner, another
  computer;
- parallel throwaway work that backend-native subagents (Claude's Task tool,
  Codex's equivalent) can't cover;
- context bloat that is measured, not expected;
- tool-choice failures or conflicting instructions that a skill or persona
  can't fix, shown by the evaluation.

Lead routes to such an instance with handoffs (#177) as in ADR 0011. To
people it looks like one more Tag. Existing Tags with their own Slack app keep
working that way, and nothing migrates automatically. Running a boundary
instance under the one Slack app, without an app of its own, needs Lead to
pass it events (the inbox design on `wip/tag-profiles`). That is a follow-up,
built when the first such instance is needed.

**Approvals (#208) show the persona.** Approval prompts are ephemeral to the
requester and carry the persona's name and picture. The requester check is
unchanged. Saved approval rules belong to the instance, not the persona.

**Tag proposes, a person approves.** In a thread, Lead may propose a new
persona, a skill or a routine. Nothing is written until the requester, who
must be an authorized user, approves it on the card. The card, expiry and
"one open proposal" checks are the ones ADR 0011 built for Tag proposals.

**Creating app Tags stays on hold.** No Tag creates a Slack app. ADR 0011's
service-account, Slack CLI and `apps.manifest.create` code stays in the
branch, unchanged and not called. Resuming it needs a new decision.

## Evaluation

Each fold or split is decided by a small end-to-end set, not by argument.

- About 10 golden tasks, each a real Slack request with the expected outcome:
  the right persona, the right skill loaded, the right tool or helper called,
  and the answer checked against a short rubric.
- The harness runs them through the bridge's backend path with a fake Slack
  client, so no live Slack is involved. It reads the backend's event stream to
  see which skill files were read and which tools ran.
- Fold one thing at a time and run the set before and after. Keep a split
  only if the folded version scores worse.
- The runs call paid models, so each run needs an agreed budget and a cost
  estimate first.

## Trade-offs

- **A persona looks like a separate agent but isn't.** It shares context,
  credentials and memory access with the other personas. The Tag's settings
  and `tag NAME persona list` say so, and anything that needs separation
  becomes a real instance.
- **No @mention per persona.** People type the name. A typo goes to Lead,
  which picks a persona.
- **One context can still overflow.** Many personas and skills add
  descriptions to every run. The evaluation shows when to split.
- **Slack shows one app** in audit logs, analytics and channel membership.
- **Pictures need a public URL**, visible to anyone who has it. Hosting
  uploads is a follow-up under [#206](https://github.com/klovr-co/hover-tag/issues/206).

## Plan for #238

1. **Lead as it exists.** Keep ADR 0011's role, routing and roster. Point
   Lead's instructions at personas and skills first, and refuse new Tag
   instances while creation is on hold. Set ADR 0011's status to "Changed by
   ADR 0012".
2. **Personas.** `tag NAME persona add|list|show|set|remove` with name,
   description, picture, skills and memory scope, and a `personas`
   capability for Tag.app. Carry over from `wip/tag-profiles`: the picture
   check and pinned characters, the name parser, thread personas and the
   message record, with their tests. Leave behind what made a profile an
   instance: `tag add --profile`, `kind: profile`, the shared Slack values and
   the inbox.
3. **Calling and display.** The name parser in the bridge, persona choice by
   the agent through a helper, the thread's persona, persona instructions and
   memory scope for the run, `username` and `icon_url` on posts, and persona
   labels in thread context. Approval prompts carry the persona.
4. **Proposals.** Lead proposes a persona, a `SKILL.md` (text only, with a
   size cap), or a routine. The requester approves on the card. Routines go to
   `tag NAME routine add --from-json` when #237's `routines` capability
   exists, and to `state/pending-routines.json` until then.
5. **Evaluation harness** with the golden tasks above, run offline. The first
   paid run waits for a budget.

Tests use fake Slack clients. No step creates, reinstalls or deletes a Slack
app. `chat:write.customize` is added to the manifest by a person, and live
checks (edits keep the name, streaming and ephemeral messages with a custom
name) wait for an explicit go-ahead.

## Follow-ups

- The skill and tool library, presets and install flow (#176).
- Hosting uploaded pictures (#206).
- Boundary instances under the one Slack app (the inbox design).
- Resuming app-Tag creation, if an installation needs a second Slack app.
- Personas in Tag.app's Details tab.
