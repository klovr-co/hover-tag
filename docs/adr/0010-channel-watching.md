# ADR 0010: Treat a run without an @mention as an untrusted-input session

- Status: Proposed
- Date: 2026-10-13
- Related: [ADR 0001](0001-credential-boundary.md),
  [ADR 0009](0009-thread-scoped-agent-conversations.md)

## Context

Tag acts only when an authorized person @mentions it or sends it a direct
message. People asked for Tag to join a conversation without a mention every
time, as a human colleague would. Draft #120 let a classifier (TypeSafe Jev)
decide when an untagged message was meant for Tag, then started a normal run.

A classifier cannot carry that weight. Anyone in the channel can write a
message, or seed a thread, that games it. Prompt injection has already
exfiltrated private data through Slack AI by planting instructions where an
assistant would read them. A watched run that started the normal agent would
give that text the full agent: shell, files, memory, connected tools and
cross-channel search.

## Decision

The classifier is a noise and cost filter, not a safety boundary. A run
without an explicit mention is an untrusted-input session with reduced
capability:

1. **No capability to misuse.** Tag drafts a watched reply with the backends'
   existing text-only mode, the same one that writes activity summaries, so
   Codex and Claude behave the same:
   - no tools, shell, MCP or web search;
   - no workspace, files, Tag memory, credentials or session resume;
   - a throwaway working directory;
   - its only input is the identity-minimized thread.

   It can't read private data, so it can't leak any. The draft must be
   `NO_REPLY`, `REACT`, or one short line. The bridge replaces anything
   longer, and anything with links or mentions, with a fixed offer.
2. **Same thread, labelled.** Replies go in the thread, never at the top level.
   Each reply says why Tag replied and has **Take this on** and
   **Don't do this here** buttons.
3. **People confirm actions in a new turn.** **Take this on**, from an
   authorized person, or an @mention starts a normal run for that person.
4. **Opt in per channel.** Watching is off by default and set per channel.
   Tag posts a visible notice before it starts, honours quiet hours, and anyone
   can turn it off.
5. **Narrow scope first.** Tag watches only:
   - threads where it was @mentioned or engaged;
   - messages that name it without an @mention.

   Watching every message in a channel would be a later, separate opt-in.
6. **Gate in stages.** Each stage is cheaper than the next:
   - deterministic filters: bots, edits, Tag's own messages, unwatched
     channels, unauthorized people, member mentions, quiet hours, limits, name
     or thread match;
   - the classifier on the thread context;
   - the text-only draft, which may still answer `NO_REPLY`.
7. **Limits.**
   - At most one unprompted reply per thread until someone engages explicitly
     (@mention, **Take this on** or 👍).
   - A daily cap per channel.
   - A cooldown after 👎 or "stop".
   - A pause on Slack 429.
   - Every failure stays silent.
8. **Tune per deployment.** Tag logs every score with its outcome. 👎, "stop"
   and **Don't do this here** become negative labels; **Take this on** and 👍
   become positive labels. The evaluation harness recommends a threshold only
   when there are 200 or more labelled messages, with precision as the target.

## Consequences

- A watched reply can only answer from the thread or offer help. Anything else
  costs one click or an @mention. That trade is intended.
- A gamed classifier can make Tag post one short, labelled, link-free line in
  the thread. It cannot run tools or reveal data the thread doesn't already
  show.
- Watching needs the `reactions:read` and `reactions:write` permissions. Tag
  requests them as optional, so they never block a start. Watching stays off
  until Slack grants them.
- Several of one owner's Tags in a shared channel can each answer the same
  message. Their limits apply separately.
- Moving to channel-wide watching, or giving watched runs any tools, needs a
  new decision.

## Amendment, 26 Oct 2026: per-channel modes and chime-in

Following ADR 0012 (Tags as personas, proposed separately), each channel now has a mode:
`off`, `when-addressed` (everything above) or `chimes-in`. In `chimes-in`
channels the first stage no longer requires a name or a Tag thread for
top-level posts: every new post from a person goes to the classifier with a
second question, whether it invites colleagues' reactions. That question is
tuned for precision (threshold 0.95 by default) because positives are rare. The
rest of this decision holds. The chime-in draft is the same text-only run with
its own instructions, offers no actions, and is dropped if unsafe. The daily
cap, quiet hours and cooldown are backstops, not the decision. The cost is one
classifier call per post, so per-channel call and spend counts are shown to
the owner.
