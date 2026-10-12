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
per-user login supervisor from ADR 0008. We also considered Temporal and
DBOS. Temporal needs its own server process on every machine. DBOS runs in
process but is a whole workflow framework whose overlap behaviour and Windows
support are not documented. Neither is needed for a few routines per Tag.

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
is refused and never overwritten; a missing file means no routines.

**Schedules.** A small form evaluated with `python-dateutil`'s `rrule` in the
routine's IANA timezone: `every 2h`, `weekdays 09:00`, `mon,fri 17:30`,
`monthly 1 09:00`, `monthly last 17:00`. Routines run at most once an hour.
Times on the hour start a stable 1–5 minutes later unless marked `exact`. A
time skipped by daylight saving runs when the gap ends; a repeated time runs
once.

**Scheduling.** Decisions use the wall clock and the saved next-due time, so
restarts, sleep, and clock changes all reduce to "is it due?". After a gap,
missed slots become one history entry and, by default, one late catch-up run
for the latest slot within seven days (`catch_up: skip` turns this off). A
routine never overlaps itself, at most two routine runs share a Tag at once,
a run cut off by a restart is marked interrupted and never replayed, and five
failures in a row pause the routine and say so in its channel.

**Approval.** Every routine records who approved it and a hash of what they
approved: name, schedule, timezone, channel, prompt, owner, limits, and
catch-up policy. Pausing does not change it. A routine whose definition no
longer matches its approval is blocked until a person saves it again. Saving
in the CLI or Tag.app is the local operator's approval. The routine's prompt
is never approval for actions during a run: risky actions still ask the
routine's owner through the scoped Slack approvals. The hash makes edits
evident, not impossible: the coding agent runs with the user's permissions
(ADR 0001).

**Running.** A run acts for the routine's owner, who must still pass the
caller policy, and posts only in the routine's channel, which must still be
one of the Tag's channels. Each run is a fresh session with a fixed preamble.
The bridge opens a thread only when there is something to post: a reply, an
approval request, or a failure. A reply of `NO_REPLY` posts nothing. Failures
post a fixed phrase and an error reference, never backend output.

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
