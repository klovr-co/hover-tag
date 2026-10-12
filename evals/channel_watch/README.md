# Channel-watching classifier evaluation

`conversations.json` holds labelled Slack conversations for the classifier that
filters channel watching (`scripts/watch_classifier.py`). Each case says whether
Tag should reply. They cover:

- messages to another person or bot;
- chat about Tag, or about someone else's Tag;
- multi-turn continuations, resumed exchanges, and offers accepted or declined;
- inclusive questions such as “anyone?”;
- look-alikes such as “tag the release”.

The labels follow the classifier's own criteria. Cases whose `note` starts with
“Debatable” are the ones where a reviewer might reasonably disagree.

The classifier is a noise and cost filter, not a safety boundary. Watched
replies have reduced capability whatever it scores; see
[ADR 0010](../../docs/adr/0010-channel-watching.md). The aim here is precision:
few unwelcome interruptions.

By default the harness uses only the cases that today's watching would send to
the classifier: messages that name Tag, or that continue a thread Tag is in. The
other cases, such as a top-level “anyone?”, wait for channel-wide watching; add
`--scope all` to include them.

```sh
# Free: estimate calls, tokens and cost per provider. No network.
python3 scripts/watch_eval.py plan

# PAID: only with a spend-capped key and an agreed budget.
OPENTAG_OPENAI_DECISIONS_API_KEY=... python3 scripts/watch_eval.py score \
  --provider openai-decisions --out evals/channel_watch/scores.jsonl \
  --max-usd 0.50 --confirm-paid

# Free: precision, recall and threshold sweep from recorded scores.
python3 scripts/watch_eval.py report evals/channel_watch/scores.jsonl
```

`score` won't run without `--confirm-paid`, a `--max-usd` of at most 5, and the
provider's key. While it runs it:

- logs a running cost and stops before the cap, assuming the next call costs at
  least as much as the last;
- stops on HTTP 401, 402, 403 and 429 instead of retrying;
- appends each answer as soon as it arrives, so rerunning the same command
  resumes without paying for finished cases.

## Tune a deployment

Each Tag logs every score, its outcome and the minimized thread to
`state/channel-watch-scores.jsonl` in its private folder. Feedback becomes
labels:

- negative: 👎, “stop” and **Don't do this here**;
- positive: **Take this on** and 👍.

```sh
python3 scripts/watch_eval.py export ~/Tag/NAME/.tag/state/channel-watch-scores.jsonl \
  --cases mine.json --scores mine-scores.jsonl
# Set "expected" on every null case in mine.json, then:
python3 scripts/watch_eval.py --fixtures mine.json report mine-scores.jsonl --target-precision 0.95
```

Export and report use the scores the Tag already paid for; they make no calls.
Feedback only arrives on messages Tag replied to, so label a sample of the
below-threshold messages by hand too, or recall will look better than it is.
`report` recommends a threshold only once at least 200 messages are labelled.
It picks the lowest threshold that meets the precision target.

## Prices

Cost estimates assume about four characters per token over the whole request
body. When the provider reports `usage.input_tokens`, the running total uses that
figure. Prices live in `watch_classifier.PROVIDERS`:

- OpenAI Decisions input is $0.10 per million tokens and output is free,
  according to the provider's guide.
- TypeSafe Jev input is $0.042 per million tokens. That price was supplied with
  this change; TypeSafe's API reference doesn't list prices.
