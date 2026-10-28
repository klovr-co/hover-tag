# ADR 0012: One agent loop; Tags are personas that bundle skills

- Status: Accepted (2026-10-26); to be superseded by [ADR 0013](0013-skills-and-levels.md), Proposed
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
- optionally a memory scope, a subset of the instance's `MFS_ALLOWED_SCOPES`;
- a voice and a proactivity level, with optional per-channel overrides (below).

Personas live in the instance's settings. They have no Slack tokens,
sessions, backend credentials or process of their own.

**Lead is the default persona.** It answers when nobody is named, and it is
the router. In practice:

- `@Tag Finance: close the month` or `@Tag ask Finance to check the invoice`
  calls Finance. The name must come first and be followed by `:` or `,`, or
  come after `ask`. The match is exact and ignores case. "ask the finance team
  whether…" is not a call.
- With no name, Lead answers itself unless the request is clearly another
  persona's area. Then Lead hands over in the thread: it posts one short line
  ("That's Finance's area, handing you over."), and Finance's answer follows
  straight after, in the same run. Lead's instructions list the personas and
  their descriptions, and the agent names its choice with a helper call.
  There is no separate classifier call, no second agent and no second model
  call; the handover costs one short extra Slack message.
- A persona never changes silently. A different name and picture always
  follow a handover line, or a person naming the persona.
- A thread remembers its persona, so a later `@Tag` without a name stays with
  it. Naming another persona (`@Tag Ops: …`) moves the thread there, which is
  also how a person corrects a wrong handover.
- Switching persona keeps the same session. The model keeps the whole thread
  in context and loads the new persona's skills. Nothing is retold, summarized
  or handed over.
- DMs work the same way.

**Voice and proactivity.** Two short fields set how a persona sounds and how
often it speaks up. Both follow the article's warning about the
"Frankenprompt" (every team adds a sentence, "resulting in conflicting rules
and no consistent voice"):

- **Voice:** free text of at most 200 characters about tone and length, for
  example "warm, upbeat colleague; short replies; light humour". It isn't a
  place for rules or procedures; those are skills. Only the active persona's
  voice goes into a run, as one line. Lead sees only names and one-line
  descriptions. A voice never overrides the Tag's standing rules (approvals,
  formatting, what it may post).
- **Proactivity:** one of three levels, each including the ones before it.
  - `only-when-asked` (default): @mentions and DMs only.
  - `when-addressed`: also messages that name the Tag or continue its
    thread, in watched channels (#233).
  - `chimes-in`: also announcements that invite reactions, such as launches,
    demos and wins (planned, below).
- **Per-channel overrides:** a persona may have a different voice or
  proactivity in a channel. The owner's channel settings are the ceiling: a
  persona can't be more proactive in a channel than the Tag is allowed to be
  there. At most one persona chimes in per channel, so a launch post gets one
  reply, not one per persona.

`tag NAME persona add|set` take `--voice TEXT` and `--proactivity LEVEL`,
plus `--channel ID` for an override. `tag NAME persona show` prints the
level that applies in each channel and, when a level can't apply, why (for
example "chimes-in needs a classifier; none configured"). Tag.app's Details
tab gets the same as a persona sheet: a one-line Voice field with a character
count, a three-way "Speaks up" control, and a per-channel list. A level that
can't apply is disabled, with the reason beside it.

**Chime-in mode (planned, with #233).** Example: "when I post a launch video,
Tag comments on it like a coworker I really like." #233 can't do this today.
Its classifier asks whether the requester is talking to the assistant, and by
design answers no to posts not addressed to Tag. Its watched replies are also
text-only, so it can't see the video. Chime-in needs:

- **Its own classifier question:** "Does this post invite colleagues'
  reactions, for example a launch, demo, release, milestone or win?" Requests,
  questions to someone else, incidents, routine status and sensitive personal
  news are no. It is tuned for precision (threshold 0.95 or higher), with its
  own threshold setting and its own labelled set, because positives are rare.
- **Every new message is asked:** in channels where a persona's level is
  `chimes-in`, each new message from a person (never a bot) goes to Jev or
  Decisions with this question. The classifier decides; there is no fixed
  number of chime-ins per day. #233 measured about $0.0001 a call ($0.00009
  with Decisions), so 1,000 messages cost about $0.09. Cost grows with channel
  traffic, so Tag.app shows calls and spend per channel. Asking both #233's
  question and this one in a single call, if the provider allows it, is
  unverified.
- **Backstops:** at most one reply per message. #233's quiet hours, its
  cooldown after 👎 or "stop", and its existing daily reply cap stay as safety
  nets, not as the decision.
- **The persona's voice:** one short threaded reply, a reaction only, or
  nothing; the draft may still decide `NO_REPLY`. A small label says Tag is
  chiming in and offers to turn it off for the channel.
- **A read-only look at the attachment:** images as images; link previews
  from the message's unfurl fields; video as a few frames (about 8), plus
  Slack's own transcript when Slack provides one, all within size caps. As far
  as we know, neither backend takes a video file directly (to be checked in
  their docs), and the chime-in run has no tools to convert one itself, so the
  bridge prepares the frames before the run. The agent then comments on what
  it sees without further setup. There is no separate transcription service.
  The run has no tools, memory, connectors, credentials or writes. Unverified:
  whether Slack exposes transcripts for uploaded videos through the API, and
  whether `ffmpeg` is on the computer for frames.

#233 would need to change in six ways:

1. Its free filters pass only messages that name Tag or sit in Tag's threads.
   In chime-in channels every new message needs to reach the classifier.
2. `watch_classifier.py` has one question (`tag_is_addressed`). It needs a
   second question with its own instructions, criteria, threshold and eval
   set.
3. Its watched draft is text-only. It needs a read-only attachment mode that
   reuses the bridge's attachment download with caps.
4. Per-channel counts of classifier calls and spend, shown in Tag.app.
5. Per-channel modes (watch or chime-in) instead of the single
   `OPENTAG_WATCH_CHANNEL_IDS` list, with the Home tab to match.
6. `watch_eval.py` needs to take the question as a parameter.

**Without a classifier key.** On #233 today, turning on watching without the
selected provider's key (TypeSafe Jev or OpenAI Decisions) is a config error,
so there is no fallback. Proposed, in this order:

1. **The Tag's own backend as the classifier,** if the owner turns it on: one
   tiny text-only call (no tools, no session) with the same question,
   answering yes or no. Self-reported confidences aren't calibrated, so there
   is no threshold. Cost: about 1,500 input and 10 output tokens a call
   (estimated from #233's state size, unverified). That is priced at the
   Tag's model rate with an API key, or taken from the plan's limits with a
   subscription sign-in. It adds a backend start to each message it checks,
   and it needs its own run on #233's eval sets, with a budget, before it is
   offered.
2. **Otherwise the feature stays off,** and Tag.app and `tag config show` say
   why and what would turn it on. @mentions and DMs work as always.

A rules-only fallback (reacting when a message names Tag or continues its
thread) was considered and dropped: on #233's 122 natural real messages those
rules were right 0.67 of the time against 1.00 for Decisions at 0.95.

Per feature:

| Feature | With Jev or Decisions | Without a key |
|---|---|---|
| Watching (`when-addressed`) | #233's free filters, then the classifier | backend if turned on; else off |
| Chime-in | its own question on every new message | backend if turned on; else off |
| Handover to a persona | not used; Lead's loop decides from the descriptions it already sees | the same, at no extra call |
| Connector narrowing | recall first, within the allow list | pass the whole allow list |

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

**Tag proposes, a person approves.** In a thread, the Tag under its own name
(Lead, on Lead's Tag) may propose a new persona or a skill. Nothing is written
until the requester, who must be an authorized user, approves it on the card
within an hour. The card, expiry and one-decision checks are the ones ADR 0011
built for Tag proposals. A skill is text only and is written only into the
Tag's own workspace. Routines need no card: #237's `tag routine add` already
approves a routine in the requester's name.

**Creating app Tags stays on hold.** No Tag creates a Slack app. ADR 0011's
service-account, Slack CLI and `apps.manifest.create` code stays in the
branch, unchanged and not called. Resuming it needs a new decision.

## Evaluation

Each fold or split is decided by a small end-to-end set, not by argument.

- About 10 golden tasks, each a real Slack request with the expected outcome.
  Pass or fail is graded on the outcome: what Tag did (its writes, from the
  fake Slack client and helper logs) and what it said, against checks and a
  short rubric. The handover is the one path check that is graded, because
  people see it: whether Lead handed over, and to whom. The skills read and
  the tools called are recorded alongside as diagnostics, not graded. The migration kit warns that
  "Path checks break the moment the design changes", while the article says
  to check "whether the agent loaded the right skill or called the right
  tool". Recording the path without failing on it serves both.
- The harness runs them through the bridge's backend path with a fake Slack
  client, so no live Slack is involved. It reads the backend's event stream to
  see which skill files were read and which tools ran.
- Each task runs at least twice. Compare which tasks flipped, not only the
  total. In the article's example, a break "only showed up in one run out of
  two".
- Fold one thing at a time and run the set before and after. Keep a split
  only if the folded version scores worse.
- The harness writes the migration kit's `results.jsonl` layout, so the kit's
  `compare.py` and `estimate.py` can be used as they are (Apache-2.0,
  dependency-free). Chime-in and watching keep their own
  labelled sets in #233's `watch_eval.py`.
- The runs call paid models, so each run needs an agreed budget and a cost
  estimate first (`estimate.py` gives the tokens; prices come from the
  provider's price list).

## Further reading and how it applies

Each source was read in full on 25 Oct 2026, except where marked.

- **Anthropic, [Building agents: When to fold subagents into your main agent
  loop](https://claude.com/resources/articles/building-agents-when-to-fold-subagents-into-your-main-agent-loop)**
  (Diane Sarkis, 22 Oct 2026). It is the basis of this ADR. Its examples are
  unnamed ("one customer", "one company"). The figures it gives are "a roughly
  2x faster runtime" for one customer's single loop, a system prompt cut "by
  over 80%", tool choice degrading past "30-50 tool definitions", and "a set
  of 10 golden examples is enough to start". *Applies:* everything in the
  Decision section.
- **Cognition, [Don't Build Multi-Agents](https://cognition.com/blog/dont-build-multi-agents)**
  (Walden Yan, 12 Jun 2025). Two principles: "Share context, and share full
  agent traces, not just individual messages" and "Actions carry implicit
  decisions, and conflicting decisions carry bad results". Its Flappy Bird
  example shows two subagents building mismatched parts. It gives no numbers.
  *Applies:* a handover between personas keeps the same session, so the full trace is
  shared. A handoff to a boundary instance passes the thread link, not a
  summary. Personas never work on one request in parallel.
- **Anthropic, [How we built our multi-agent research
  system](https://www.anthropic.com/engineering/multi-agent-research-system)**
  (Hadfield et al., 13 Jun 2025). An Opus 4 lead with Sonnet 4 subagents
  "outperformed single-agent Claude Opus 4 by 90.2% on our internal research
  eval". Agents use "about 4× more tokens than chat interactions, and
  multi-agent systems use about 15× more", and on BrowseComp "token usage by
  itself explains 80% of the variance". It pays off for "breadth-first
  queries" and tasks valuable enough for the cost. It is a poor fit where
  agents "share the same context or involve many dependencies", such as "most
  coding tasks". Its evals started with "about 20 queries", an LLM judge with
  a rubric, and "end-state evaluation". *Applies:* Tag's requests are mostly
  conversational and share context, so one loop. Breadth-first research stays
  with the backend's own subagents inside a run, not personas. The golden set
  grades end states.
- **Anthropic, [Building multi-agent systems: When and how to use
  them](https://claude.com/resources/articles/building-multi-agent-systems-when-and-how-to-use-them)**
  (Cara Phillips, 23 Jan 2026). Three situations favour several agents:
  context pollution, parallel tasks and specialization. One kind of
  specialization: "Different tasks sometimes require different personas,
  constraints, or instructions that conflict when combined." Multi-agent
  setups "typically use 3-10x more tokens". It says to split by context, not
  by kind of work, which otherwise becomes a "telephone game". Its tool-count
  signal is "often 20+", lower than the October article's 30–50. *Applies:*
  if two personas' voices or rules conflict when combined, that is a reason
  for a separate instance, which is why a voice stays one line. Connector
  narrowing (#233 follow-up) should start before about 20 tools, not 30.
- **Anthropic, [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)**
  (Erik S. and Barry Zhang, 19 Dec 2024). It starts from "finding the simplest
  solution possible", and adds complexity "only when it demonstrably improves
  outcomes". Routing "works well for complex tasks where there are distinct
  categories that are better handled separately, and where classification can
  be handled accurately". The page now notes that much of its tooling advice
  has changed since then. *Applies:* #233's classifier is a narrow routing
  gate (answer or stay silent), and chime-in adds one more category. Lead's
  handover is routing too, so the golden tasks measure it.
- **H. Floyd, [Your Multi-Agent System Is an Org
  Chart](https://harryfloyd.substack.com/p/your-multi-agent-system-is-an-org)**
  (7 Jul 2026). **Read only in part:** the post is paywalled after its
  opening. Its subtitle reads "Cognition said don't build them. Anthropic said
  do. A year on, they converge on the one question that decides it." The rest,
  including its answer, is unverified, so this ADR relies on none of it. The
  org-chart point is taken from the October article ("Conway's law at work").
- **[anthropics/single-agent-loop-kit](https://github.com/anthropics/single-agent-loop-kit)**
  (Apache-2.0, "Reference implementation. Not maintained"). It is a Claude
  Code skill (audit, baseline, merge, fold, as-tool and upkeep steps), four
  dependency-free scripts, a reference loop, and a support-desk example. The
  example has a router, six subagents and 19 golden tasks (`golden.jsonl`).
  *Usable for us:* the golden-task check format (`answer_includes`,
  `actions`, `forbidden_actions`, `answer_excludes`,
  `only_expected_writes`); the `results.jsonl` layout; `compare.py` (a change
  "does not hold" if it completes fewer tasks, plus flipped tasks and changed
  writes); `estimate.py` (token estimates before a paid run); and offline
  smoke runs with a fake model. *Not usable as is:* its runner is tied to the
  example's backend, so ours drives the bridge instead. The audit steps
  assume existing subagents, and we have none. The kit itself says "You have
  no subagents yet. Start with one loop."

## Trade-offs

- **A persona looks like a separate agent but isn't.** It shares context,
  credentials and memory access with the other personas. The Tag's settings
  and `tag NAME persona list` say so, and anything that needs separation
  becomes a real instance.
- **No @mention per persona.** People type the name. A typo goes to Lead,
  which answers or hands over.
- **A wrong handover is visible.** If Lead hands a shift question to Finance,
  people see it. The golden tasks include requests with and without a
  handover, and check that Lead hands over only when it should (graded, unlike
  the other path diagnostics). People correct it by naming the persona.
- **One context can still overflow.** Many personas and skills add
  descriptions to every run. The evaluation shows when to split.
- **Speaking up unasked can annoy people.** Chime-in is off by default,
  tuned for precision, and stops after a 👎 or "stop". A missed launch costs less than an unwanted comment.
- **Slack shows one app** in audit logs, analytics and channel membership.
- **Pictures need a public URL**, visible to anyone who has it. Hosting
  uploads is a follow-up under [#206](https://github.com/klovr-co/hover-tag/issues/206).

## Plan for #238

1. **Lead as it exists.** Keep ADR 0011's role, routing and roster. Point
   Lead's instructions at personas and skills first, and refuse new Tag
   instances while creation is on hold. Set ADR 0011's status to "Changed by
   ADR 0012".
2. **Personas.** `tag NAME persona add|list|show|set|remove` with name,
   description, picture, skills, memory scope, voice and proactivity
   (including per-channel overrides), and a `personas` capability for
   Tag.app. Until #233 lands, only `only-when-asked` takes effect; `persona
   show` says so for the other levels. Carry over from `wip/tag-profiles`: the picture
   check and pinned characters, the name parser, thread personas and the
   message record, with their tests. Leave behind what made a profile an
   instance: `tag add --profile`, `kind: profile`, the shared Slack values and
   the inbox.
3. **Calling and display.** The name parser in the bridge, Lead's handover
   (helper call, handover line, then the persona's reply in the same run),
   the thread's persona, persona instructions and
   memory scope and voice for the run, `username` and `icon_url` on posts,
   and persona labels in thread context. Approval prompts carry the persona.
   Until streaming under a custom name is checked live, a Tag with personas
   posts its replies whole instead of streaming them.
4. **Proposals.** The Tag proposes a persona or a `SKILL.md` (text only, up
   to 8,000 characters). The requester approves on the card. Routines use
   #237's `tag routine add`, approved in the requester's name.
5. **Evaluation harness** with the golden tasks above, in the migration
   kit's results layout, run offline. The first paid run waits for a budget.

Chime-in, the no-key fallback and the classifier follow-ups belong to #233
and its follow-up PRs, not #238. #238 only stores the fields they read.

Tests use fake Slack clients. No step creates, reinstalls or deletes a Slack
app. `chat:write.customize` is added to the manifest by a person, and live
checks (edits keep the name, streaming and ephemeral messages with a custom
name) wait for an explicit go-ahead.

## Follow-ups

- The skill and tool library, presets and install flow (#176).
- Hosting uploaded pictures (#206).
- Boundary instances under the one Slack app (the inbox design).
- Resuming app-Tag creation, if an installation needs a second Slack app.
- Personas in Tag.app's Details tab, including the voice, "Speaks up" and
  per-channel controls.
- Chime-in mode and the no-key fallback, on #233 (six changes listed above).
- Transcripts for uploaded videos, if Slack offers none through the API.
