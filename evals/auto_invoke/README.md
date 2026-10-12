# Untagged-reply classifier evaluation

`conversations.json` holds labelled Slack conversations for the optional
untagged-reply classifier (`scripts/auto_invoke.py`). Each case says whether Tag
should reply. The cases cover messages to another person or bot, chat about
Tag, multi-turn continuations, resumed exchanges, inclusive questions such as
“anyone?”, and look-alikes such as “tag the release”. The labels follow the
classifier's own criteria. Cases with a `note` marked “Debatable” are the ones
where a reviewer might reasonably disagree.

The harness turns each case into Slack-shaped messages with placeholder member
IDs and runs the bridge's real state builder. It then scores the result with one
provider and reports precision, recall, F1 and a threshold sweep. Refusals,
errors and unscored cases count as “stay silent”, which is what the bridge does.

```sh
# Free: estimate calls, tokens and cost per provider. No network.
python3 scripts/auto_invoke_eval.py plan

# PAID: only with a spend-capped key and an agreed budget.
OPENTAG_OPENAI_DECISIONS_API_KEY=... python3 scripts/auto_invoke_eval.py score \
  --provider openai-decisions --out evals/auto_invoke/scores.jsonl \
  --max-usd 0.50 --confirm-paid

# Free: precision, recall and threshold sweep from recorded scores.
python3 scripts/auto_invoke_eval.py report evals/auto_invoke/scores.jsonl
```

`score` refuses to run without `--confirm-paid`, a `--max-usd` of at most 5, and
the provider's key. It logs a running cost and stops before the cap, assuming
the next call costs at least as much as the last. It also stops on HTTP 401, 402,
403 and 429 instead of retrying. Each answer is appended as soon as it arrives,
so rerunning the same command resumes without paying for finished cases.

Cost estimates assume about four characters per token over the whole request
body. When the provider reports `usage.input_tokens`, the running total uses that
figure. Prices live in `auto_invoke.PROVIDERS`: OpenAI Decisions input is
$0.10 per million tokens with free output, from the provider's guide. The
TypeSafe Jev input price of $0.042 per million tokens was supplied with this
change; TypeSafe's API reference does not list prices.
