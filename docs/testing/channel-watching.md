# Channel watching: change notes and evaluation

These notes go with the experimental channel-watching change, which supersedes #120. The design rationale is in
[ADR 0010](../adr/0010-channel-watching.md) and operator setup in
[the Slack adapter reference](../../references/slack-adapter.md#channel-watching-experimental).

## What changed

- **Opt-in per channel, off by default.** Each channel is `off`, `when-addressed` or
  `chimes-in`, set from the Home tab or `OPENTAG_WATCH_CHANNEL_MODES`. `tag start` moves channels from the older
  `OPENTAG_WATCH_CHANNEL_IDS` list to `when-addressed`, once. Tag posts a
  visible notice before it starts watching a channel.
- **Narrow scope.** Tag watches only messages that name it, and threads where it was @mentioned or engaged.
- **Reduced capability.** A watched reply is a text-only backend run with no tools, files, memory, credentials or
  session. It stays in the thread and says why Tag replied. **Take this on** or an @mention starts a normal run.
- **Stages.** Deterministic filters run first, then the classifier on the thread context (the first message plus
  the nine before the current one), then the draft, which may still decide `NO_REPLY`.
- **Limits.** One unprompted reply per thread until someone engages, a daily cap, quiet hours, a cooldown after 👎
  or "stop", and a pause on Slack 429.
- **Classifier per Tag.** `OPENTAG_WATCH_CLASSIFIER` chooses TypeSafe Jev (`noul`) or OpenAI Decisions (one
  `predicate`). Each has its own private key, model and threshold, and the agent never inherits them.
- **Migration 5.** It adds the `message.channels`, `message.groups` and `reaction_added` events, plus the
  optional `reactions:read` and `reactions:write` permissions. Watching stays off until Slack grants them. Tests
  cover older installs, retries and repeated runs.
- **Tuning.** Every score is logged privately. Feedback becomes labels, and `scripts/watch_eval.py` recommends a
  threshold only after 200 or more labelled messages.

## CLI and Tag.app

- **CLI.** The settings are in `tag config` and the `tag settings` → Advanced menu.
- **Slack Home tab.** Per-channel opt-in.
- **Tag.app.** No UI change. Setup checks and the "Update app" flow come from the shared Python, so the new
  events and permissions already appear there. A per-channel watch toggle in Tag.app is a follow-up, once
  thresholds are confirmed in the sandbox.

## Evaluation

| Set | Cases | Licence or origin |
|---|---|---|
| Fixtures | 45 | Synthetic, written for this change |
| Real Slack cases | 235 | Approved channels; private, aggregates only |
| Public IRC cases | 1,223 | Kummerfeld et al. 2019, CC BY 4.0 |
| Synthetic hard cases | 35 | Written for this change |

Results:
[`results-2026-10-13.md`](../../evals/channel_watch/results-2026-10-13.md) and
[`results-2026-10-13-real.md`](../../evals/channel_watch/results-2026-10-13-real.md). Spend across the session
was $0.188.

- **Provisional default.** OpenAI Decisions at 0.95, to be confirmed by the sandbox trial.
- **Inflated agreement.** Both labelling passes were done by agents built on the same model family, so
  agreement and Cohen's κ (0.99 real, 0.81 IRC) measure consistency, not correctness. Human spot checks of the
  review lists are still needed.
- **Few natural positives.** Only about 10 real positives are natural messages; the rest are derived from
  @mentions.
- **Shortened history.** The thread history stored for real cases was shortened, and most misses come from that
  context. Rerunning the real subset with full context costs about $0.03.
- **Excluded from decisions.** AMI meeting data (κ 0.30, because addressing the room isn't inviting a reply) and
  the 5 human stand-in cases.
