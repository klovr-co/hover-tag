# ADR 0010: Run routines in each Tag's Slack bridge

- Status: Accepted
- Date: 2026-10-13
- Relates to: ADR 0001 (credential boundary), ADR 0002 (local process
  supervision), ADR 0007 (self-contained Tag folders), ADR 0008 (login
  supervisor); issue #31

## Context

Tags only worked when someone mentioned them. Operators want a Tag to do
useful work on a schedule, such as a weekly digest posted in a channel, and
other components (a Tag that sets up other Tags, #232) need to give a new Tag
starting routines. A scheduled run has no person waiting, so it must still be
bounded, auditable, approved by a person, and unable to post anywhere it was
not meant to.

Two processes could own the schedule: each Tag's Slack bridge, or the
per-user login supervisor from ADR 0008. We also considered Temporal, DBOS
and APScheduler. Temporal needs its own server process on every machine
(about 45 MB, plus a 15 MB SDK), replays and retries work by default, and runs
a repeated daylight-saving time twice. DBOS runs in process but needs Postgres
drivers even for SQLite, and replays every missed slot or none. APScheduler
runs a repeated time twice and a skipped one an hour late. None is needed for a
few routines per Tag, so the scheduler is small and takes its rules, and its
test scenarios, from the projects that solved the same problem: Temporal
schedules, Kubernetes CronJobs, Hermes Agent and OpenClaw.

## Decision

**Where.** Each Tag's bridge runs its own routines with a small scheduler
thread. The bridge already holds the Slack client, the caller policy, the
backend request path, Activity, and the in-memory scoped approvals (#208), so
a routine run goes through the same path as a mention and nothing new receives
a Slack token. The login supervisor stays a process keeper: it keeps the
bridge alive, and the bridge keeps the schedule. A stopped Tag runs no
routines; the CLI and Tag.app say so.

**Storage.** Definitions live in the Tag's private home as
`.tag/routines.json`, schema version 1, so they move with the Tag folder.
Run state, history (last 100 runs), and run-now requests live under
`.tag/state/routines/` and are written by the bridge. A file from a newer Tag
is refused and never overwritten; a missing file means no routines. A damaged
routine entry is kept as it is when the others are saved, and a damaged state
file is set aside as `<id>.corrupt-<time>.json` with a note in history. Writes
are flushed to disk before they replace a file.

**Schedules.** A small form in the routine's IANA timezone: `every 2h`,
`weekdays 09:00`, `mon,fri 17:30`, `monthly 1 09:00`, `monthly last 17:00`.
Run times come from cronsim, which follows Debian cron. Routines run at most
once an hour. Times on the hour start a stable 1–5 minutes later unless
marked `exact`. A time skipped by daylight saving runs when the gap ends,
unless another of the routine's times follows within half an hour; a repeated
time runs once. Intervals count elapsed time, so `every 1d` moves an hour
against the clock when daylight saving changes; use `daily 09:00` to keep the
clock time.

**Scheduling.** Decisions use the wall clock and the saved next-due time, so
restarts, sleep, and clock changes all reduce to "is it due?". After a gap,
missed slots become one history entry and, by default, one late catch-up run
for the latest slot within seven days (`catch_up: skip` turns this off), as a
Kubernetes CronJob runs its most recent missed time. (Temporal runs the
earliest missed time instead; for a report, the latest is the useful one.) A
routine never overlaps itself. At most two routine runs share a Tag at once;
another routine that comes due waits for a free slot, keeping at most one
waiting run. A newer slot replaces it, and it is dropped if it became too late
or its schedule changed. Each routine's state changes under a lock, so ticks
and finishing runs never overwrite each other. A slot chosen to run is saved in
the same write that moves the next-due time, so a crash between the two can't
lose it, and each scheduled run has one id per slot, so a slot already in
history never runs again. A run cut off by a restart is marked interrupted and
never replayed. Five failures in a row, interrupted runs included, pause the
routine and say so in its channel; resuming starts the count again. A run that
can't run at all, because its channel or owner is no longer allowed, pauses it
at once. A rate-limited run is tried once more a minute later and doesn't
count. A run still going ten minutes past its time limit is released so later
runs aren't skipped forever. Run-now requests older than ten minutes are
dropped. The scheduler saves when it last checked and any errors, so the CLI
and Tag.app can say when routines aren't being checked. `tests/soak/routine_soak.py` checks all of this against a real
bridge process with a scripted backend and a clock that jumps like a laptop
waking up.

**Approval.** Every routine records who approved it and a hash of what they
approved: name, schedule, timezone, channel, prompt, owner, limits, and
catch-up policy. Pausing does not change it. A routine whose definition no
longer matches its approval is blocked until a person saves it again. Saving
in the CLI or Tag.app is the local operator's approval. The routine's prompt
is never approval for actions during a run: risky actions still ask the
routine's owner through the scoped Slack approvals. The hash makes edits
evident, not impossible: the coding agent runs with the user's permissions
(ADR 0001). When the coding agent changes routines for someone in Slack, the
change is approved in that person's name and only the routine's owner can make
it; a routine's own run can't add or change routines.

**Running.** A run acts for the routine's owner, who must still pass the
caller policy, and posts only in the routine's channel, which must still be
one of the Tag's channels. Each run is a fresh session with a fixed preamble.
The bridge opens a thread only when there is something to post: a reply, an
approval request, or a failure. A reply of `NO_REPLY`, alone or on its own
first or last line, posts nothing; an empty reply is a failure. A routine
paused or removed during its run posts nothing, and a run someone stops is
recorded as stopped, not failed. Failures post a fixed phrase and an error
reference, never backend output.

**Limits.** Each run has a turn limit, a spend limit, a wall-clock limit, and
a token limit for when cost is unknown. Claude enforces turns and spend
natively (`max_turns`, `max_budget_usd`); the bridge also stops either backend
from its usage events. Codex has no native turn or spend limit, so the bridge
enforces spend from its configured token prices, or tokens when there are
none.

## Consequences

- Routines follow the same credential boundary, caller policy, and approvals
  as mentions, for both backends.
- Laptops that sleep miss runs; history and Tag.app show it, and one catch-up
  keeps reports from silently skipping a day.
- Run history and Activity show every run, including missed, skipped, and
  blocked ones, without posting noise to Slack.
- Proposing routines from Slack, webhook triggers, tool profiles, and daily or
  monthly spend caps are separate follow-ups; they build on this schema.
