---
name: live-slack-test
description: Run a live end-to-end test of Tag in the shared Slack sandbox with the test Tags. Use when asked to test in the sandbox, test live, test in Slack, verify a handoff or reply end to end, or confirm a bridge change works with real Slack.
---

# Live Slack test

Run the change from this worktree against real Slack. Do this yourself, in the
main session. Don't hand it to a subagent unless the user asks.

Unit tests come first. A live test is needed only for behavior that crosses
Slack. Examples: replies, threads, plan or task cards, handoffs between Tags,
placeholders, reactions, and reconnects.

## The sandbox

The sandbox details are private and live outside the repo, in
`~/.config/tag-sandbox/sandbox.md`. That file has the org, workspace, and
channel IDs, the human account, the Keychain entry for the password, and the
test Tags with their Tag IDs and App IDs. Read it first. If it is missing, stop
and ask the user for it. Don't search for the values or guess them.

Never write the password, a bot token, or the sandbox IDs into a repo file, a
commit, or a PR. Logs and screenshots go in `.context/`, which git ignores.

Sandboxes expire. If sign-in fails or the org is gone, stop and tell the user.
Recreating it needs their Slack developer account. Afterwards, update the
private file, not this skill.

## Steps

1. **Check the Tags.** Run `./tag <tag-id> status` for each Tag the test needs.
   If a Tag is missing, create it with
   `python3 .agents/skills/live-slack-test/scripts/add_test_tag.py "<name>" <codex|claude> <org-id> <workspace-id>`
   from the repo root, and add its row to the private file.
2. **Run this worktree's code.** Other worktrees share these Tags. Stop any
   Tag another session left running, then start each Tag from *this* worktree
   with a reloading bridge:
   `./tag <tag-id> dev > .context/dev-<tag-id>.log 2>&1` (run it in the background).
   Wait until the log shows `Slack  Connected`.
3. **Post as the human.** Tags ignore messages from bots, so the prompt must
   come from the human account. Sign in with the `browser-use` skill, using the
   human account and Keychain password from the private file. Open the test channel and send the prompt,
   for example `@<Tag A> ask @<Tag B> and @<Tag C> what 2+2 is`.
   If Slack asks for an emailed code, ask the user for it.
4. **Watch the result.** Read the thread with
   `.agents/skills/live-slack-test/scripts/read_thread.sh <tag-id> <channel-id>`
   and follow the logs: `.context/dev-<tag-id>.log` and `./tag <tag-id> logs`.
   Take a screenshot of the thread in the browser.
5. **Test both backends** when the change depends on the backend (see "Agent
   backend parity" in `AGENTS.md`). Switch a Tag with
   `./tag <tag-id> settings ai sign-in codex|claude`, or use one Tag of each
   backend.
6. **Clean up.** Stop the `dev` processes you started. Leave the Tags and their
   Slack apps in place. Never delete a test Tag's Slack app without the fresh
   confirmation that `AGENTS.md` requires.

## Report

Tell the user:

- the prompt you sent, and the Tags and backends you used
- what each Tag posted, from `read_thread.sh`, with the screenshot saved in
  `.context/` and shown inline
- any errors from the logs
- anything you skipped or could not check
