# Control your Tag

You choose who can ask your Tag to work, which sources it can use, and when to
stop a task. Some Codex actions also ask for a decision while the task is running.

## Respond to a Codex approval request

Codex normally reviews actions that need additional permissions automatically.
If a supported request reaches Tag, it pauses for your decision and shows
**Codex needs approval** with a short action category. You will not see this
prompt for every task.

### Example: update the budget spreadsheet in your shared folder

You ask Tag to add this month's expenses to the budget spreadsheet in your
team's shared folder. The folder is available on the computer running Tag,
and your agent has a tool that can edit the spreadsheet.

If the shared folder is outside the locations Codex is allowed to write to,
the edit may need additional file access. When Codex sends Tag a file-change
approval request, you see this private message in Slack:

> **Tag**
>
> **Codex needs approval** to change files outside the workspace sandbox.
> Approve only if you expect this request.
>
> **Approve once** · **Deny**

Choose **Approve once** if you expected Tag to edit the shared budget. Choose
**Deny** if you want to review the changes first; you can then ask Tag to
prepare a separate draft in its workspace. Approval does not grant access
that your local account lacks or change the shared folder's permissions for
other people.

### Other actions that may need approval

| Your task | Why approval may be needed |
| --- | --- |
| Update your team's weekly report in its existing folder | The report is outside the locations Codex can write to. |
| Extract the figures from a scanned invoice | A required document tool needs to be installed using additional permissions. |
| Refresh a report with the latest sales export | The download command needs network access that the current sandbox does not allow. |

These are possible triggers, not a fixed list of tasks that always show a
prompt. Existing permissions and Codex's automatic review determine whether
the action is allowed, refused, or sent to you for a decision. Tag shows these
buttons only when Codex sends it a supported approval request.

### Make your decision

1. Read the action category and check that it matches the work you requested.
   The Slack prompt does not include the raw command, file paths, or full
   permission details. If you cannot tell whether the action is expected,
   choose **Deny**.
2. Select **Approve once** to allow that pending request, or **Deny** to refuse
   it. Approval applies to that request; it does not automatically approve later
   requests.
3. Tag replaces the controls with **Approved once** or **Denied**. Codex
   continues with your decision. Denying an action may prevent it from
   completing the task; it does not itself stop the entire task.

In a channel, only the person who started the task sees the approval prompt.
In a direct message, it appears in that conversation. Only the original
requester can decide, and they must still be authorized to use Tag there.

If Tag says the request has expired or was already decided, the old buttons
cannot approve it. Review the task's latest status before starting another
request. Unsupported or unavailable approval requests are refused rather than
approved automatically by Tag.

## Stop a task

Use Slack's **Stop** control to interrupt an active Codex task. Stopping a task
does not undo files it already changed or actions it already completed. Check
the result before asking Tag to try again.

Approval buttons and Stop require Codex App Server, Tag's default Codex
connection. They are not available with the legacy Codex exec or Claude backends.
With Codex App Server, use [Activity](../reference/supported-capabilities.md#review-task-activity)
to review the available tool steps and results.

## Choose who and what Tag can access

By default, only you can give your Tag instructions. See
[Sharing access](sharing-access.md) before authorizing another person.
[What Tag knows](what-tag-knows.md) explains the conversations and indexed
sources available to it; [Adding integrations](adding-integrations.md) covers
connected tools and accounts.

Tag uses the local account running its backend, subject to the backend's
permissions. Its workspace folder alone does not restrict access to other files,
and approval prompts do not appear before every action. See
[Your own Tag](access.md#the-work-happens-on-your-computer) for the execution
model and ways to limit the files and accounts available on the host.
