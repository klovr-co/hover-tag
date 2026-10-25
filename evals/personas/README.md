# Persona golden tasks

Ten end-to-end tasks for personas ([ADR 0012](../../docs/adr/0012-tags-as-personas.md)).
Each one sends real Slack-style requests through the bridge with a fake Slack
and grades the outcome: what Tag did (handovers and proposals), what it said,
and who answered. A wrong handover is visible to people, so who answered is
graded; which persona answered is also recorded as `by_agent`.

```sh
python3 evals/personas/run_eval.py --variant smoke          # fake model, offline, $0
python3 evals/personas/run_eval.py --variant baseline --real-backend --budget-usd 2   # paid
```

Each task runs twice by default; compare which tasks flipped, not only the
total. Results use the layout of Anthropic's
[single-agent-loop-kit](https://github.com/anthropics/single-agent-loop-kit),
so its `skill/scripts/compare.py` compares two runs as it is:

```sh
PYTHONPATH=single-agent-loop-kit/skill/scripts \
  python3 single-agent-loop-kit/skill/scripts/compare.py evals/personas/results baseline v1
```

The fake model follows each task's `fake` script, so a smoke run checks the
harness and the bridge's plumbing, not the model's choices. A real-backend run
calls the configured model once per turn (13 turns per rep) and refuses to start
without `--budget-usd`. It never uses a real Slack token.
