# Results: real and public labelled sets, 2026-10-13

These are aggregate metrics only. The cases themselves are private. Real Slack cases come from approved klovr-co
channels, and public cases from the Ubuntu IRC disentanglement data (CC BY 4.0; Kummerfeld et al. 2019).

All 2,988 calls scored (1,494 per provider): `jev-1.13.0` and `gpt-6-luna`, with no errors or refusals. Spend
was **$0.184**: TypeSafe $0.061 for 1.46M input tokens, OpenAI Decisions $0.123 for 1.23M. The session total,
including the earlier fixture run, is $0.188.

## Subsets after cleaning

| Subset | Cases | Should reply | Two-labeller agreement (κ) |
|---|---|---|---|
| A: real Tag channels, plus 5 stand-in cases | 235 | 97 | 99.6% (0.99)* |
| A: natural messages only | 122 | 10 | |
| A: derived from @mentions | 113 | 87 | |
| C: public IRC | 1,223 | 699 | 90.7% (0.81)* |
| D: synthetic hard cases | 35 | 9 | 94.6% (0.86)* |

\* Both labelling passes were done by agents built on the same model family, so agreement and κ are
**inflated**. They measure consistency, not truth. Human spot checks of the review lists are still needed.

AMI meeting data was not scored. Its group-addressee mapping disagreed with the second labeller (κ 0.30), because
speech addressed to the room isn't the same as a reply being welcome.

## Precision / recall by threshold

| Subset | Provider | 0.5 | 0.7 | 0.8 | 0.9 (default) | 0.95 |
|---|---|---|---|---|---|---|
| A: all real | Decisions | .86 / .92 | .93 / .89 | .93 / .85 | **.94 / .83** | **.99 / .74** |
| A: all real | Jev | .52 / .89 | .72 / .61 | .81 / .39 | .93 / .27 | 1.0 / .10 |
| A: natural only | Decisions | .48 / 1.0 | .60 / .90 | .64 / .90 | .69 / .90 | .89 / .80 |
| A: natural only | Jev | .13 / 1.0 | .33 / 1.0 | .50 / .70 | .86 / .60 | 1.0 / .30 |
| A: derived only | Decisions | .96 / .91 | .99 / .89 | .99 / .84 | .99 / .82 | 1.0 / .74 |
| A: derived only | Jev | .87 / .87 | .94 / .56 | .94 / .36 | .95 / .23 | 1.0 / .08 |
| A: current watching scope (103) | Decisions | | .98 / .89 | | .99 / .83 | 1.0 / .75 |
| A: current watching scope (103) | Jev | | | | .92 / .26 | 1.0 / .10 |
| C: IRC | Decisions | | .97 / .58 | .97 / .54 | .99 / .44 | .99 / .36 |
| C: IRC | Jev | | .95 / .84 | .97 / .72 | .99 / .32 | 1.0 / .04 |
| D: synthetic | Decisions | | .75 / 1.0 | .75 / 1.0 | .75 / 1.0 | .80 / .89 |
| D: synthetic | Jev | | .75 / 1.0 | .82 / 1.0 | .89 / .89 | 1.0 / .56 |

## Score distributions

- **Decisions is nearly binary on every subset.**
  - On real A, 80 of 97 positives score ≥ 0.9 and 90 of 138 negatives score < 0.1.
  - Its misses are confident: on IRC, 111 positives score < 0.1. Those IRC positives are mostly replies to a
    human who stands in for Tag, which Decisions seems to read as not addressing an assistant.
- **Jev is graded and compressed.** On real A, positives spread over 0.3–1.0 and negatives over 0.1–0.8, with a
  heavy overlap between 0.4 and 0.8.
  - On IRC it separates well around 0.7–0.8, so its usable threshold is far below 0.9.
  - On real Slack it doesn't separate well at any threshold: the best F1 is 0.66.

## Where the errors are (real A)

| Provider and threshold | False positives | False negatives |
|---|---|---|
| Decisions @ 0.9 | 5: 2 natural asides, 3 replies to another person | 17: 11 derived continuations, 5 derived direct, 1 natural inclusive |
| Decisions @ 0.95 | 1 natural aside | 25: mostly derived continuations |
| Jev @ 0.9 | 2 (talk about Tag) | 71: 56 derived continuations |

Most misses are derived continuations: a Tag mention removed inside a thread whose Tag replies were **shortened**
in the stored history. The results are therefore sensitive to context. Rerunning A with full thread history
(about $0.03) would show whether Jev's gap on Slack is partly an artefact of the shortening.

## Provisional recommendation

**OpenAI Decisions with a threshold of 0.95.** On all real cases this gives precision 0.99 and recall 0.74; in
today's watching scope, 1.0 and 0.75. Use 0.9 if you'd rather trade precision for recall (0.94 / 0.83).

This is provisional:
- Subset A has only **10 natural positives**. On natural messages alone, Decisions at 0.95 makes 1 false positive
  in 112 negatives and finds 8 of the 10 positives.
- Most real positives are derived from @mentions.
- Thread history in subset A was shortened.
- The labels come from agents, not people.

The sandbox trial, and the 200+ labelled messages from a deployment's own score log, should confirm or move it.
If Jev is used, its threshold must be retuned per deployment, around 0.7–0.8; the 0.9 default is wrong for it.
