# Results: classifier prompt variants, 2026-10-14

This run compares prompt variants for the channel-watching classifier, optimising for **precision**: a wrong
unprompted reply costs more than a missed one. All figures are aggregates; the cases are private. The variants
are in `scripts/watch_prompt_variants.py`, and production prompts are unchanged.

## Method

- **Tuning set:** half of the cleaned public IRC set (612 cases, fixed seed) plus the 35 synthetic hard cases.
- **Held-out sets:** the other IRC half (611) and real set A (235 Slack cases: 122 natural, 10 of them should
  reply). Each was scored **once** at the end, for the best variants only.
- **Baseline:** scores reused from the 2026-10-13 run, which used the same prompt and model.
- **Variants** (both providers unless noted):
  1. Baseline: the current prompt.
  2. Short: one question plus terse TRUE and FALSE bullets.
  3. Two questions: "addressed to Tag?" and "reply welcome now?"; the score is the lower of the two.
  4. Few-shot: the baseline plus six fixed synthetic examples.
  5. Jev bullets (TypeSafe only): the same criteria as separate items.
- **Metrics:** precision at recall ≥ 0.5 (P@R.5), and the best recall at precision ≥ 0.95 (R@P.95) with the
  threshold that reaches it, over thresholds from 0.00 to 0.99.

## Tuning (IRC-dev 612 + synthetic 35)

Each cell shows P@R.5 / R@P.95 @ threshold.

| Variant | Decisions, IRC-dev | Jev, IRC-dev | Jev, synthetic |
|---|---|---|---|
| Baseline | .975 / .625 @ .56 | .984 / .817 @ .74 | 1.0 / .556 |
| Short | .927 / .417 @ .94 | .970 / .623 @ .79 | 1.0 / .667 |
| Two questions | .874 / .172 @ .93 | .928 / .482 @ .76 | 1.0 / .667 |
| Few-shot | **.989 / .718 @ .49** | .989 / .820 @ .70 | 1.0 / .889 |
| Jev bullets | — | .990 / .808 @ .73 | 1.0 / .889 |

- **Decisions on synthetic:** no variant reaches 0.95 precision, because its false positives there are confident.
- **Short and two questions** lost recall at high precision with both providers.
- **Chosen for held-out scoring:** Decisions few-shot, Jev few-shot and Jev bullets.

## Held-out results

| Set | Variant | P@R.5 | R@P.95 @ threshold |
|---|---|---|---|
| IRC held-out (611) | Decisions baseline | .977 | .663 @ .44 |
| IRC held-out (611) | Decisions few-shot | **.991** | **.782 @ .33** |
| IRC held-out (611) | Jev baseline | .984 | .901 @ .60 |
| IRC held-out (611) | Jev few-shot | .978 | .887 @ .62 |
| IRC held-out (611) | Jev bullets | .979 | .907 @ .59 |
| Real A, all (235) | Decisions baseline | 1.00 | .784 @ .91 |
| Real A, all (235) | Decisions few-shot | 1.00 | **.835 @ .83** |
| Real A, all (235) | Jev baseline | .726 | .237 @ .91 |
| Real A, all (235) | Jev few-shot | .792 | .196 @ .93 |
| Real A, all (235) | Jev bullets | .694 | .258 @ .91 |

### At deployable thresholds (real A)

| Variant | Threshold | Precision | Recall | False positives | Natural false positives (of 112) | Natural positives found (of 10) |
|---|---|---|---|---|---|---|
| Decisions baseline | 0.90 | .94 | .82 | 5 | 4 | 9 |
| Decisions few-shot | 0.90 | **.97** | .80 | **2** | **1** | 8 |
| Decisions baseline | 0.95 | .99 | .74 | 1 | 1 | 8 |
| Decisions few-shot | 0.95 | .99 | .75 | 1 | **0** | 8 |
| Jev baseline | 0.80 | .81 | .39 | 9 | 7 | 7 |
| Jev few-shot | 0.80 | .77 | .53 | 15 | 13 | 8 |
| Jev bullets | 0.80 | .79 | .42 | 11 | 9 | 8 |

On held-out IRC at 0.9 and 0.95, Decisions few-shot and baseline are equal (precision .99, recall .43–.44 and
.34–.36). The few-shot gain on IRC sits at thresholds around 0.3, which we would not deploy.

## Conclusions

- **Decisions few-shot is the only variant that improves on held-out data.**
  - At the 0.9 default it cuts false positives on real A from 5 to 2 (natural messages: 4 to 1), for one fewer
    natural positive found.
  - At 0.95 it makes no natural false positives, against 1 for the baseline.
  - The evidence is thin: the natural differences are 1 to 3 messages.
- **Jev variants don't help.** Few-shot and bullets match the baseline on held-out IRC and are no better on real
  Slack; few-shot nearly doubles natural false positives at 0.8.
- **Short and two-question prompts hurt both providers.**
- **No change to the code default.** The suggestion to confirm in the sandbox is Decisions with the few-shot
  prompt at 0.95.

## Caveats

- **Thresholds.** The best thresholds differ a lot between sets (Decisions few-shot: 0.33 on IRC, 0.83 on real
  A), so choose them per deployment, not from IRC.
- **Small natural sample.** Real A has 10 natural positives; derived cases have shortened thread history; labels
  come from agents built on the same model family, so their agreement is inflated.
- **Possible leakage.** The few-shot examples are synthetic and differ from every tuning case. Some share
  structure with the synthetic tuning set (a reply to a bot), which may flatter few-shot there.
- **No caching.** The Decisions API rejects `prompt_cache_key` (HTTP 400, unknown parameter) and reported 0
  cached tokens on every call. The fixed prompt prefixes are under the size providers usually need for caching.

## Spend

The task total was **$0.425** of its $0.80 cap: tuning $0.255, held-out $0.170, smoke tests and one diagnostic
call under $0.001. Prices: Decisions $0.10 per 1M input tokens, Jev $0.042 per 1M; output is free. There were
no errors, refusals or quota stops. The session total is $0.613.
