# ADR 0013: A Tag is one identity with skills; skills have versions and levels

- Status: Proposed
- Date: 2026-10-28
- Issue: [#232](https://github.com/klovr-co/hover-tag/issues/232), PR [#238](https://github.com/klovr-co/hover-tag/pull/238)
- Supersedes: [ADR 0012](0012-tags-as-personas.md) (personas)
- Related: [ADR 0001](0001-credential-boundary.md), [ADR 0011](0011-lead-tag.md),
  channel watching [#233](https://github.com/klovr-co/hover-tag/pull/233),
  routines [#237](https://github.com/klovr-co/hover-tag/pull/237)
- Spec: Falconer, "Tag Skills and Levels" (agreed 27 Oct 2026, scoring updated 28 Oct 2026)

## Context

ADR 0012 gave one Tag several named personas over one agent loop. People
found that it added choices (which persona do I call?) and work for us
(routing, handovers, per-persona memory) without a real boundary behind
them. Anthropic's 22 Oct 2026 article, which ADR 0012 followed, still holds:
one agent loop with skills is the default. What people want to see is what a
Tag can do and how proven each ability is.

## Decision

**A Tag is one identity.** It has a name, picture, description, personality,
proactivity and model. What it can do comes from its skills. There are no
personas, handovers or persona names on posts.

- **Personality:** free text of at most 200 characters about tone and length
  (`OPENTAG_PERSONALITY`). It is one line in the run's instructions and never
  overrides the Tag's standing rules.
- **Proactivity:** `only-when-asked` (default), `when-addressed` or
  `chimes-in` (`OPENTAG_PROACTIVITY`). Per-channel modes and the classifier
  belong to #233. Without a Jev or Decisions key, the fallback order is still
  to be confirmed (see Open decisions).

**Each skill shows one badge: its version and its level**, for example
"launch-feedback v3 · ⭐ Lv 7".

- **Version, from saved changes.** A new skill is `v1`. Each saved change to
  the skill's files that differs from the last saved version adds 1. A save
  that changes nothing doesn't count, and neither does Tag writing the version
  field itself. The version is stored in the skill, as `metadata.version` in
  the `SKILL.md` frontmatter, so it travels when the skill is copied. A new
  version doesn't change the level.
- **Level, from use.** A new skill is Lv 1 with 0 XP. A run that used the
  skill and went well gives it +10 XP; every skill a run used gets the XP.
  Going from Lv n to n+1 takes 100 × n XP (10 good runs for Lv 2, 90 for
  Lv 10). The level gates nothing. XP changes on every run, so it is kept in
  the Tag's own records, not in the skill: a copied skill keeps its version
  and starts again at Lv 1 in the new Tag.
- **A run went well** when it finished without an error, its reply wasn't
  deleted and nobody reacted 👎. Deletions and reactions arrive after the
  reply, so XP is granted after a settling period (proposed: 10 minutes) and
  not at all if either happens first. Reactions need `reactions:read`, which
  #233 adds; without it, only errors and deletions count.

```yaml
---
name: launch-feedback
description: Gives friendly feedback on launch videos and posts
metadata:
  version: "3"
---
```

Values under `metadata` are strings, as the Agent Skills format expects.

**Both skill loaders accept the field.** Codex 0.163.0's parser
(`codex-rs/skills/src/parser.rs` at `rust-v0.163.0`) reads only
`metadata.short-description` and ignores other keys; it doesn't deny unknown
fields. Claude Code 2.1.304, which the Agent SDK runs, lists `metadata` among
its known frontmatter keys. Tests read the written frontmatter back the way
each loader does.

**Where a Tag's skills live.** One copy per skill in the Tag's own workspace,
`.agents/skills/NAME/`, which Codex reads natively. Claude receives the same
folder as a local plugin, the way Tag already gives it the `tag-*` runtime
skills (see Open decisions). Tag never writes into a folder of yours
(`OPENTAG_WORKDIR`): skills there are shown, but get no version written.
Tag's own `tag-*` skills change with releases and get no badge.

**Specialties are parts of a skill.** Neither backend has nested skills, so
a skill can keep extra instruction files in a `specialties/` folder, and its
`SKILL.md` says when to open each one (progressive disclosure):

```
launch-feedback/
  SKILL.md          says when to open each specialty
  specialties/
    video.md        opened only when the post is a video
    blog-post.md    opened only for blog posts
  scripts/
```

- Tag lists the `*.md` files directly in `specialties/`. A specialty's name is
  the `name:` line at the top of its file if there is one, else its file name
  in words (`blog-post.md` is "Blog post").
- A specialty has no version or level of its own. It is one of the skill's
  files, so editing it makes the skill's next version, and opening it during
  a run counts as using the skill.
- Tag.app lists them under the skill: "launch-feedback v3 · ⭐ Lv 7 ·
  Specialties: Video, Blog post". The Slack footer shows only the skill.
- Nothing in either backend loads them by itself, so a skill whose
  `SKILL.md` never mentions a specialty never uses it. `tag NAME skill list`
  flags a specialty its `SKILL.md` doesn't name.

**How Tag notices a new version.** For each skill, a hash of all its files
(specialties and scripts included) with the `metadata.version` line removed, kept with the level in
`state/skills.json`. Tag compares hashes:

- after every run, since the agent may edit a skill during a request;
- when Tag writes a skill (an approved proposal or `tag NAME skill …`);
- when the Tag starts, which catches edits made in git.

A changed hash adds 1 to the version and writes it into `SKILL.md`. Undoing a
change counts, because the files differ from the last saved version. A skill
Tag hasn't seen before starts at `v1` unless its file already carries a
version.

**How Tag knows which skills a run used.** Both backends' events already
reach Tag's event normalizers, which add one event,
`{"type": "skill_used", "name": NAME}`, at most once per skill per run:

- **Claude (Agent SDK):** a `Skill` tool call names the skill in its input
  (`plugin:skill` for plugin skills); a `Read` of a file under a skill folder
  also counts. The input key is checked against a recorded event in tests
  before it is relied on.
- **Codex (App Server):** there is no skill event in the v2 protocol; Codex
  detects skill use internally from shell commands
  (`codex-rs/skills/src/invocation.rs`). Tag does the same from the
  `commandExecution` item it already receives: a `commandActions` entry of
  type `read` whose path is `…/skills/NAME/SKILL.md` or a file in that skill's
  folder, or a command that runs a script inside a skill folder. A skill named
  in the request's own input (`{"type": "skill"}`) also counts.

The bridge collects these events per run, adds the footer and records the run
for XP. Runs that used no skill change nothing.

**Where the badge shows.**

- **Slack:** the reply's small footer, for example "used launch-feedback
  v3 · ⭐ Lv 7", one entry per skill used.
- **Tag.app:** the Tag's skill list in Details, each skill with its badge and
  a thin bar to the next level, from `tag NAME skill list --json` behind a new
  `skills` capability.

**Tag proposes a skill, a person approves.** From ADR 0012, only the skill
part stays: the Tag may propose a `SKILL.md` (text only, up to 8,000
characters) in a thread, and the requester approves it on a card within an
hour. Proposing specialties with a skill is a follow-up. Routines use #237's `tag routine add`.

**Kept from ADR 0011 and 0012:** Lead's routing, Tag creation on hold, and
the golden-task harness (rewritten with skill tasks). Separate instances only
for a real boundary.

## Trade-offs

- **Skill use is inferred.** Codex reports reads and commands, not skill use,
  so a skill read for another reason also earns XP. That matches Codex's own
  analytics, and a misread costs 10 XP.
- **"Went well" is a proxy.** No 👎 isn't approval. It is cheap and visible,
  and it can be tuned once there is data.
- **A version is any saved change,** good or bad. The level, from use, shows
  whether a version works.
- **XP waits about 10 minutes,** so a badge in the footer shows the level
  before this run's XP.

## Plan for #238

1. Remove the persona code and docs; keep the merge, Lead's creation hold and
   skill proposals. Add personality and proactivity to the Tag's settings.
2. `tag_skills.py`: find skills, read and write `metadata.version`, hashes,
   XP and levels in `state/skills.json`; `tag NAME skill list --json` and the
   `skills` capability, with each skill's specialties.
3. `skill_used` events in both normalizers; the bridge collects them, adds the
   footer, and grants XP after the settling period unless the reply was
   deleted or got 👎.
4. Tag.app skill list with badges, progress and specialties.
5. Golden tasks for skills, offline with the fake model; paid runs need a
   budget.

## Open decisions

1. Fallback without a classifier key: "rules only, then the Tag's own
   backend, then off" (27 Oct) or "the Tag's own backend if turned on, else
   off" (26 Oct, which dropped rules only).
2. One skill folder (`.agents/skills`), with Claude reading it as a plugin and
   showing names as `plugin:skill`, instead of a copy in `.claude/skills`.
3. The settling period before XP (proposed: 10 minutes).
