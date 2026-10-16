# Results: deterministic rules vs the classifier, 2026-10-15

How much do two free, deterministic rules catch on their own, and what does the paid classifier add? These are
aggregates only. The cases and labels are the same cleaned sets as before, and the classifier figures reuse the
existing OpenAI Decisions **few-shot** scores, so there were **no new API calls ($0)**.

## Rules

- **R1, name.** The message names the Tag as a word outside code, using the existing `names_tag` check against
  the case's assistant name.
- **R2, continuation.** Skipping the requester's own trailing messages, the latest earlier message in the
  thread is by Tag, and the requester spoke before that Tag message.

`scripts/watch_eval.py rules` reproduces these numbers.

## Precision / recall (true positives, false positives)

| Set | R1 name | R2 continuation | R1 or R2 | Classifier @0.9 | Classifier @0.95 |
|---|---|---|---|---|---|
| Real A, all (235; 97 reply) | .955 / .216 (21, 1) | .906 / .794 (77, 8) | .911 / .948 (92, 9) | .975 / .804 (78, 2) | .986 / .753 (73, 1) |
| Real A, natural (122; 10 reply) | — / .000 (0, 1) | .750 / .600 (6, 2) | .667 / .600 (6, 3) | .889 / .800 (8, 1) | **1.00 / .800 (8, 0)** |
| Real A, derived (113; 87 reply) | 1.00 / .241 (21, 0) | .922 / .816 (71, 6) | .935 / .989 (86, 6) | .986 / .805 (70, 1) | .985 / .747 (65, 1) |
| IRC held-out (611; 344 reply) | .953 / .238 (82, 4) | 1.00 / .535 (184, 0) | .980 / .584 (201, 4) | .993 / .433 (149, 1) | 1.00 / .340 (117, 0) |
| IRC dev (612; 355 reply) | .916 / .245 (87, 8) | 1.00 / .518 (184, 0) | .963 / .583 (207, 8) | .993 / .428 (152, 1) | .991 / .315 (112, 1) |
| Synthetic hard (35; 9 reply) | .280 / .778 (7, 18) | 1.00 / .444 (4, 0) | .308 / .889 (8, 18) | .750 / 1.00 (9, 3) | .750 / 1.00 (9, 3) |

## Combining rules and classifier

| Set | Rules OR classifier @0.9 | Rules OR classifier @0.95 | Rules AND classifier @0.9 | Rules AND classifier @0.95 |
|---|---|---|---|---|
| Real A, all | .913 / .969 (94, 9) | .913 / .969 (94, 9) | .974 / .784 (76, 2) | .986 / .732 (71, 1) |
| Real A, natural | .727 / .800 (8, 3) | .727 / .800 (8, 3) | .857 / .600 (6, 1) | 1.00 / .600 (6, 0) |
| IRC held-out | .982 / .814 (280, 5) | .985 / .759 (261, 4) | 1.00 / .203 (70, 0) | 1.00 / .166 (57, 0) |
| IRC dev | .970 / .814 (289, 9) | .967 / .738 (262, 9) | 1.00 / .197 (70, 0) | 1.00 / .161 (57, 0) |
| Synthetic hard | .333 / 1.00 (9, 18) | .333 / 1.00 (9, 18) | .727 / .889 (8, 3) | .727 / .889 (8, 3) |

"Rules AND classifier" means the classifier only sees what a rule lets through.

## True positives only the classifier catches

These are counts of true positives where neither rule fired.

| Set | Threshold | Inclusive ("anyone?") | Implicit address (Tag in the thread, R2 doesn't fire) | Other | Classifier-only false positives |
|---|---|---|---|---|---|
| Real A, all | 0.9 | 1 | 0 | 1 | 0 |
| Real A, all | 0.95 | 1 | 0 | 1 | 0 |
| IRC held-out | 0.9 | 42 | 5 | 32 | 1 |
| IRC held-out | 0.95 | 40 | 3 | 17 | 0 |
| IRC dev | 0.9 | 37 | 3 | 42 | 1 |
| IRC dev | 0.95 | 36 | 2 | 17 | 1 |
| Synthetic hard | 0.9 and 0.95 | 1 | 0 | 0 | 0 |

## What it shows

1. **R2 finds almost all implicit address.** Positives that only the classifier catches inside a Tag thread are
   0 on real A and 3–5 on held-out IRC.
2. **The rules aren't precise enough to reply on their own.** On real A, R1 or R2 makes 9 wrong replies against
   the classifier's 1–2. On natural messages, the rules make 3 of 112 wrong, against 0 at 0.95.
   - R1 alone is risky when the Tag is called "Tag": on the synthetic set it fires on "tag" as a verb and on
     talk about Tag (18 false positives).
3. **OR lowers precision to the rules' level.** On real natural messages it falls from 1.00 to .73, for no extra
   recall.
4. **What only the classifier adds is mainly inclusive questions.** On IRC held-out at 0.9 that's 42 of 79
   classifier-only true positives, plus some "other" addressing that neither rule sees. On real natural messages
   the classifier finds 8 of 10 positives, the rules 6 of 10.

## Caveats

- **R2 is flattered by construction.** IRC positives come from reply links, which closely match "Tag wrote the
  last message", and real A's derived positives were built by removing a mention inside a Tag thread. The
  honest comparison is real A natural, which has only 10 positives.
- **Single-person labels.** Labels come from agents built on the same model family, and their agreement is
  inflated.
- **Production is broader than R2.** Its free filters already let through any message in a thread where Tag was
  mentioned or engaged, plus any message that names Tag. R2 is a narrower variant of that.
