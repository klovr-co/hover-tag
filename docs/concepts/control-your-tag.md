# Control your Tag

You can stop an active Codex task from Slack. If Codex asks for extra access,
Tag sends the approval request privately to the person who started the task.

## Review an approval request

Suppose you ask Tag to update a budget spreadsheet in a shared Finance folder:

> @Tag update /shared/Finance/budget.xlsx with the approved forecast.

If Codex asks for write access to that folder and supplies the reason shown
below, the private approval message displays these fields:

> **Tag**
>
> Codex needs approval to use additional filesystem or network access.
>
> Requested: /shared/Finance
>
> Why: This folder is outside the permitted write locations.
>
> Approve only if you expect this request.
>
> Details · Approve once · Deny

Select **Details** to inspect the actual path or permission Codex supplied. A
file change request may instead show a requested write root; a legacy patch
request can show up to three file paths. A command request identifies a known
executable and its working directory, while withholding arguments that could
contain credentials or file contents. A permission request can show requested
filesystem paths or network access. Tag shows Codex's reason when present and
explicitly says when details or a reason were not provided.

Check that the target and reason match what you asked for. **Approve once**
lets that one pending Codex request continue. **Deny** declines it. Neither
button changes general permissions. The request expires if the task ends or
the approval times out; reopening its details or pressing a stale button
cannot revive it. Only the original requester can inspect or decide it.

Codex normally reviews sandbox boundary actions automatically, so editing a
spreadsheet does not always produce an approval. Tag cannot verify what a
command's withheld arguments will do; if the visible context is insufficient,
deny and ask Tag to explain or narrow the action.

## Stop an active task

Use Slack's **Stop** control while Codex is working. Tag interrupts the active
turn and shows the task as stopped. Work the backend already completed may
remain, so inspect the files or external service before restarting the task.
