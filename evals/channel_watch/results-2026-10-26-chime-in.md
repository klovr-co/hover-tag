# Results: the chime-in question, 2026-10-26

How well does the `invites_reactions` question pick out posts that invite colleagues' reactions (launches, demos,
releases, milestones, wins)? Aggregates only; the real posts are private.

## Sets

| Set | Posts | Positive | Origin |
|---|---|---|---|
| Real, agreed | 279 | 8 | Top-level posts from the approved Slack channels, scrubbed (mentions, URLs). Two labelling passes; the 10 disagreements and 8 marked unclear were dropped (agreement 281 of 291, κ 0.63) |
| Synthetic | 77 | 25 | `chime-in-synthetic.json`, written by hand; over-represents positives, so never used alone to set a threshold |

The existing real set A (requests and Tag exchanges) wasn't relabelled: chime-in only asks about top-level
posts, and almost all of set A are thread replies or addressed to Tag.

## TypeSafe Jev (jev-1.13.0)

| Set | Threshold | Precision | Recall | True positives | False positives |
|---|---|---|---|---|---|
| Real, agreed (275 scored, 4 errors) | 0.90 | 1.00 | .625 | 5 | 0 |
| Real, agreed | **0.95 (default)** | **1.00** | .375 | 3 | 0 |
| Real, agreed | 0.85 | .667 | .750 | 6 | 3 |
| Synthetic | 0.90 | .957 | .880 | 22 | 1 |
| Synthetic | **0.95 (default)** | **.950** | .760 | 19 | 1 |
| Synthetic | 0.97 | 1.00 | .600 | 15 | 0 |

- **No wrong chime-ins on real posts at 0.9 or 0.95.** Below 0.9, false positives appear quickly (3 at 0.85,
  13 at 0.5). Nearly all are shared links and other companies' launches (at 0.85: 2 competitor posts and 1 share; at 0.5: 7 shares and 3 competitor posts).
- **The synthetic false positive** at 0.95 is a competitor's launch, which the label says is not the team's news.
- **Misses at 0.95** (5 of 8 real positives) are short posts of 4 to 12 words: a win and a demo scored 0.90 to
  0.93, a launch with an image 0.86, a demo video 0.62, and one demo errored. Three carry a file, and the
  classifier sees only its description, not the contents. On the synthetic set the worst miss is a launch post
  that is only a video (0.27).

## OpenAI Decisions

Not run: the evaluation key now returns HTTP 401. The run stopped on the first call and nothing was spent. It
needs a new spend-capped key.

## Cost

Jev only: 352 calls, about **$0.009** at $0.042 per 1M input tokens (docs.typesafe.ai/models.md). Decisions: $0.

## What it shows and caveats

- **Keep the default at 0.95.** It made no wrong chime-ins on real posts, and lowering it to 0.9 found 2 more
  real positives with none wrong, but 8 real positives is far too few to tune on.
- **Very few real positives.** About 3% of real top-level posts are news to react to, so precision matters
  most and recall estimates are rough.
- **Labels come from the same model family** (me and one labelling sub-agent), so agreement is inflated.
- **4 real posts errored** (network) and count as silent.
