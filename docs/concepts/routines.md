# Routines

A routine is work your Tag does on a schedule without anyone asking: a saved
prompt, when to run it, and the one channel where it posts the result. For
example, every Monday at 09:00, summarise the pull requests merged last week
in #launch.

## Add a routine

In Tag.app, open the Tag, choose **Routines**, then **Add a routine**. In the
terminal:

```sh
tag research routine add --name "Weekly PR digest" \
  --schedule "mon 09:00" --timezone Europe/London --channel "#launch" \
  --prompt "Summarise the pull requests merged in hover-tag since the last run. Reply NO_REPLY if there are none."
```

Saving a routine approves exactly what you saved. If anything in it changes
later without being saved through Tag.app or the CLI, the routine stops
running until someone saves it again.

| Field | Meaning |
| --- | --- |
| Name | How the routine appears in Slack, history, and Activity. |
| Schedule | When it runs; see below. |
| Timezone | An IANA name such as `Europe/London`. Defaults to this computer's. |
| Channel | One of the Tag's channels. It never posts anywhere else. |
| Prompt | What to do each time. Each run starts fresh, so say everything it needs. |
| Owner | The person the routine acts for. They must be allowed to use the Tag, and approval requests during a run go to them. Defaults to the Tag's only allowed user. |

## Schedules

| Write | Runs |
| --- | --- |
| `every 2h`, `every 90m`, `every 1d` | At that interval, counted from when you saved it |
| `daily 09:00` | Every day |
| `weekdays 09:00,13:30` | Monday to Friday, twice |
| `weekends 10:00` | Saturday and Sunday |
| `mon,wed,fri 17:30` | On those days |
| `monthly 1 09:00`, `monthly last 17:00` | On that day of the month (1–28, or last) |

Routines run at most once an hour. A time on the hour, such as 09:00, starts a
few minutes later (the same few minutes every time) so routines don't all
start at once; add `exact` to the end to keep it on the hour. When the clocks
go forward past a routine's time it runs when the gap ends, and when they go
back it runs once. Intervals such as `every 1d` count hours, so after a clock
change they run an hour earlier or later by the clock; use `daily 09:00` to
keep the time.

## What a run does

Each run starts a new conversation with the routine's prompt and a short
preamble: when the last successful run finished, whether this run is late, and
that a reply of `NO_REPLY` means there is nothing to post.

- **A reply** is posted as a new message in the routine's channel, headed with
  the routine's name.
- **`NO_REPLY`** posts nothing, also when the reply adds a short note on
  another line. The run still appears in history. An empty reply counts as a
  failure.
- **An action that needs approval**, such as pushing code, asks the owner in a
  thread in the routine's channel, exactly as during a normal request. If
  nobody answers, the action is denied. The routine's prompt is never
  approval.
- **A failure** posts a short message with an error reference. Details stay
  in `tag NAME logs` and the error report. When the AI provider's usage limit
  is reached, Tag tries once more a minute later and says nothing unless that
  fails too.
- **Pausing or removing** a routine while it runs means its reply isn't
  posted.

A run can't add or change routines. When you ask the Tag in Slack to change a
routine, it can change only routines you own, and the change is approved in
your name.

Each run is limited to $0.50 of estimated spend and 20 minutes by default, and
to 2,000,000 tokens when Tag can't estimate cost. A Tag on Claude also stops a
run after 40 turns; Codex has no turn count Tag can limit, so its runs are
bounded by spend, tokens and time. Change them with `--max-budget-usd`,
`--max-turns`, `--timeout-minutes`, and `--max-tokens`, or the spend limit in
Tag.app. Claude enforces turns and spend itself; for Codex, Tag estimates spend
from the token prices in [API connections](../reference/api-connections.md)
and stops the run.

## When runs don't happen

A routine runs only while its Tag is running. If the computer was asleep or
the Tag was stopped, Tag records the missed runs and, when it is back, runs
the latest one once if it was due in the last seven days. Use
`--catch-up skip` for routines that are only useful on time.

| History entry | Meaning |
| --- | --- |
| Posted / Nothing new | The run finished. |
| Failed, Stopped at its spend or time limit | The run stopped. Five failures in a row pause the routine and say so in its channel; resuming it starts the count again. If its channel or owner is no longer allowed, it pauses at once. |
| Missed | The computer was asleep or the Tag was stopped. |
| Skipped | The previous run was still going, or a newer run replaced it while it waited. A Tag runs two routines at a time; others wait their turn, one waiting run per routine. |
| Interrupted | The Tag stopped during the run. It isn't run again, so nothing posts twice, and the channel isn't told. It counts as a failure, so a routine that stops the Tag every time is paused. |
| Stopped | Someone stopped the run, or paused or removed the routine while it ran. A pause or removal stops the run within seconds, so it spends no more. |
| Needs approval | The routine changed after it was approved. Save it again to approve it, then resume it. |

If a running Tag stops checking its routines, for example because
`routines.json` can't be read, `tag NAME routine list` and Tag.app say so.

`tag NAME routine show NAME` lists the next five run times and the latest
runs, and `tag NAME routine runs NAME` pages through them all (`--limit`,
`--offset`). For each run they show what it did: its reply, how many steps it
took, how long, what it cost and which model ran it, the command that lists its
steps, and, for a failure, the error reference. In Tag.app, open a routine and
then a run to see the same, with its files, its steps, a link to the message
it posted in Slack, and, for a failure, a link to its lines in the Logs tab.
**Show all runs** goes past the latest five.

Runs also appear in the Tag's Activity, labelled with the routine's name;
click the label to open the routine. Tag keeps the latest 50 routine runs
there so they never push out people's requests; older runs keep their outcome,
reply link and cost, but not their steps.

## Manage routines

| Task | Tag.app | Terminal |
| --- | --- | --- |
| List | Routines | `tag NAME routine list` |
| Inspect | Open a routine, then a run | `tag NAME routine show ROUTINE`, `tag NAME routine runs ROUTINE` |
| Add | Add a routine | `tag NAME routine add …` |
| Change | Edit | `tag NAME routine edit ROUTINE --schedule …` |
| Pause or resume | The routine's switch | `tag NAME routine pause\|resume ROUTINE` |
| Run once now | Run now | `tag NAME routine run-now ROUTINE` |
| Remove | Remove | `tag NAME routine remove ROUTINE` |

A Tag can have 10 active routines, and a channel 3. Other components can create
routines with `tag NAME routine add --from-json FILE`, which takes the whole
definition as one JSON object and records the same local approval.

## Where routines are kept

Definitions are in the Tag's private folder, `~/Tag/NAME/.tag/routines.json`,
so they move with the Tag. Run state and history are in
`.tag/state/routines/`. See [ADR 0010](../adr/0010-routines-run-in-the-bridge.md)
for the design.
