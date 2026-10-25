# ADR 0012: One Tag, one agent loop, skills instead of profiles

- Status: Proposed
- Date: 2026-10-25
- Issue: [#232](https://github.com/klovr-co/hover-tag/issues/232), PR [#238](https://github.com/klovr-co/hover-tag/pull/238)
- Related: [ADR 0001](0001-credential-boundary.md), [ADR 0005](0005-multiple-tag-instances.md),
  [ADR 0011](0011-chief-of-tag.md), skills [#176](https://github.com/klovr-co/hover-tag/issues/176),
  routines [#237](https://github.com/klovr-co/hover-tag/pull/237),
  handoffs [#177](https://github.com/klovr-co/hover-tag/pull/177),
  approvals [#208](https://github.com/klovr-co/hover-tag/pull/208)
- Source: Anthropic, [Building agents: When to fold subagents into your main agent loop](https://claude.com/resources/articles/building-agents-when-to-fold-subagents-into-your-main-agent-loop) (22 Oct 2026)
- Changes ADR 0011: specialists are skills first; creating Tags stays on hold.
- Supersedes the earlier draft of this ADR ("One Slack app, with Tags as
  profiles"), kept on branch `wip/tag-profiles`.

## Context

ADR 0011 gives an installation many Tags, with Chief-of-Tag routing each
request to one owner and creating new Tags when a lane is missing. The first
draft of this ADR kept that shape but made the specialists cheaper: one Slack
app, with each specialist a "profile" (its own name, picture, instructions,
memory and backend sessions) that Chief-of-Tag dispatched to by name.

Anthropic's article on folding subagents into the main loop argues for the
opposite default:

- One model with one context, loading skills as it needs them, often beats a
  set of specialist agents on quality, cost and latency.
- Specialist knowledge (procedures, rules, judgment calls) fits in skills. The
  model picks the skill. No router, dispatcher or "which agent" classifier is
  needed.
- Splitting by topic or team copies the org chart into the system (Conway's
  law). Every hand-over loses context, like a game of telephone.
- Hard limits belong inside tools (approvals, validation), not in the
  boundaries between agents.
- Split only for a measured reason, and keep the split only if folding it back
  scores worse.

The profile draft added a dispatcher, thread ownership, an inbox between
processes, a message index, and per-profile identity on every post. That is a
lot of machinery for what is mostly a set of instructions.

## Decision

**Default to one Tag with one agent loop.** A Tag answers each request itself,
in one backend session per thread (ADR 0009). It loads skills progressively
from its working folder. Hard limits stay inside tools: backend approvals
(#208), the bridge's checks on helpers, and validation in `tag_*` commands.

**Specialists are skills, not Tags or profiles (#176).** A procedure, a set of
rules or a judgment call becomes a skill in the Tag's working folder
(`.agents/skills/` for Codex, `.claude/skills/` for Claude). There is no
router and no "which profile" step. The model reads each skill's description
and loads the one it needs. Skills are not split by topic or team.

**A separate Tag instance (ADR 0005) only for a real boundary:**

- **Context bloat that is measured**, not expected: the folded version's
  scores drop because the context is too full.
- **Parallel, discardable work.** Backend-native subagents (Claude's Task
  tool, Codex's equivalent) usually cover this inside one Tag, so it rarely
  needs a second Tag.
- **Tool choice failures or conflicting instructions** that a skill can't fix,
  shown by the evaluation below.
- **A permission or credential boundary**: production access, a different
  owner, or another computer. ADR 0001 is the line.

Handoffs (#177) and routing stay only between such real instances.

**Persona display is optional and cosmetic.** A skill may declare a display
name and icon. When the agent works under that skill, the bridge posts its
replies with Slack's `chat:write.customize` (`username`, `icon_url`). It is
still one Tag, one context and one voice: no separate memory, sessions,
credentials or routing. Display is off unless a person adds the scope to the
Tag's app and reinstalls it. Icons are built-in characters by a pinned
`raw.githubusercontent.com` URL or a public HTTPS URL; hosting uploaded
pictures stays a follow-up under
[#206](https://github.com/klovr-co/hover-tag/issues/206).

**Tag proposes, a person approves.** A Tag may propose a new skill or routine
for itself in the thread. Nothing is written until the requester, who must be
an authorized user, approves it. That uses the approve/reject card, expiry and
"one open proposal" checks ADR 0011 built for Tag proposals.

**Kept from ADR 0011 and the first draft:**

- One Slack app per installation is the default. Existing Tags with their own
  app keep working; nothing migrates automatically.
- Creating app Tags is on hold. No Tag creates a Slack app. The
  service-account, Slack CLI and `apps.manifest.create` code stays in the
  branch, unchanged and not called from any proposal flow. Resuming it needs a
  new decision.

**Chief-of-Tag, honestly.** With one Tag per installation, Chief-of-Tag has
nothing to do: there is no other Tag to route to, and Tag creation is on hold.
It helps only an installation that already runs several real instances, where
it routes a request to the one that holds the right boundary. #238 keeps the
designation and its routing as they are (already written and tested), adds
nothing to them, and doesn't use the role to gate skill or routine proposals:
each Tag proposes for itself. If no installation needs routing, the role can be
removed in a later change.

## Evaluation

Every fold or split is decided by a small end-to-end set, not by argument.

- About 10 golden tasks, each a real Slack request with the expected outcome:
  the right skill loaded, the right tool or helper called, and the answer
  checked against a short rubric.
- The harness runs them through the bridge's backend path with a fake Slack
  client, so no live Slack is involved. It reads the backend's event stream to
  see which skill files were read and which tools ran.
- Fold one thing at a time and run the set before and after. Keep a split only
  if the folded version scores worse.
- The runs call paid models, so each run needs an agreed budget and a cost
  estimate first.

## Trade-offs

- **One context can still overflow.** A Tag with many skills reads only their
  descriptions until it loads one, but long threads and big skills add up.
  The evaluation is what shows when to split.
- **No per-specialist memory, sessions or credentials.** Anything that needs
  them is a boundary, so it gets a real Tag.
- **No @mention per specialist.** People ask the Tag, and the model picks the
  skill.
- **Persona display can mislead.** A display name looks like a separate agent
  but isn't. Replies keep the app's APP label, and the Tag's settings show the
  skills that declare a display.
- **Slack's `chat.update` takes no identity fields**, so an edited message may
  lose its display name. This hasn't been checked live.

## Plan for #238

1. **Chief-of-Tag as it exists.** No new code. Point its standing
   instructions at skills first: a proposal for a new Tag is refused while
   creation is on hold, and the agent offers a skill instead. Update ADR 0011's
   status to "Changed by ADR 0012".
2. **Skill proposals.** A helper,
   `tag_skill.py propose --name NAME --description TEXT --body-file FILE`,
   saves a proposal in the Tag's `state/skill-proposals/`. After the reply,
   the bridge posts an approve/reject card with the skill's name, description
   and a preview. On approval it writes `SKILL.md` to both skill folders of
   the Tag's working folder. Limits: a valid unique name, a size cap, text
   only (no scripts or other files; those wait for #176's library), one open
   proposal, 60-minute expiry, approval by the requester only. Changing an
   existing skill is a new proposal that shows the diff.
3. **Routine proposals.** `tag_skill.py propose-routine` takes the object that
   `tag NAME routine add --from-json` accepts (name, schedule, prompt,
   channel, owner). The same card is used. On approval the bridge runs
   `tag NAME routine add --from-json` when the `routines` capability exists
   (#237). Until #237 merges, it saves the routine to
   `state/pending-routines.json` as ADR 0011 does, and says so.
4. **Optional display per skill.** A skill may declare a display name and
   icon in its frontmatter. The agent names the skill it is working under with
   a helper call; the bridge then adds `username` and `icon_url` to that run's
   posts, if the app has `chat:write.customize`. Without the scope, replies
   post as the Tag. The exact frontmatter key is checked against both
   backends' skill loaders first, so neither rejects the file.
5. **Evaluation harness** with the golden tasks above, run offline with a
   fake Slack client. The first paid run waits for a budget.

Tests for each step use fake Slack clients. No step creates, reinstalls or
deletes a Slack app, and nothing runs against live Slack without an explicit
go-ahead.

## Follow-ups

- The skill and tool library, presets and install flow (#176).
- Hosting uploaded display pictures (#206).
- Resuming app-Tag creation, if an installation needs a second Slack app.
- Removing Chief-of-Tag if no installation needs routing between real
  instances.
