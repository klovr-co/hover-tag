# Control your Tag

You choose who can ask your Tag to work, which sources it can use, and when to
stop a task. Some agent actions also ask for a decision while the task is running.

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
> Choose the scope you want to allow.
>
> **Allow once** · **Allow for this task** · **Deny** · **Deny and stop**

Choose **Allow once** if you expected Tag to edit the shared budget. Choose
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

1. Read the action category and any proposed permission or rule details.
   If the action is not what you expected, choose **Deny**.
2. Select one of the choices Codex supports for that request:

   | Choice | Effect |
   | --- | --- |
   | **Allow once** | Approve the pending command or file change. |
   | **Allow for this turn** | Grant the requested permissions for the current agent turn. |
   | **Allow for this task** | Use Codex's session-scoped approval for this Tag task, including follow-up turns. It does not carry over to later Slack requests. |
   | **Always allow this prefix** | Save Codex's proposed command-prefix rule for this Tag's future matching commands. |
   | **Always allow this host** / **Always deny this host** | Save Codex's proposed network rule for that host, for this Tag. |
   | **Deny** | Refuse the action; Codex may continue with another approach. |
   | **Deny and stop** | Refuse the action and interrupt the agent turn. |

   Not every request offers every choice. Tag respects Codex's explicit choice
   list when provided. Persistent choices appear only when Codex proposes a rule;
   review the complete prefix or host in the private prompt before confirming it.
   Tag saves these rules in the Tag's own Codex rules, not your global ones, so
   they affect only this Tag's future requests. See
   [Choose what a Tag may do without asking](#choose-what-a-tag-may-do-without-asking).
3. Tag replaces the controls with an acknowledgment that your choice was sent
   to Codex. The task's activity and final response show what happened next.

If automatic review already denied an action, supported denials instead offer
**Approve retry** / **Dismiss**. The private prompt identifies the denied action
and shows Codex's stated reason. Common credentials and URL credentials/query
parameters are redacted, and long details are marked as truncated or omitted.
Terminal input content is withheld. If Codex provides no reason, Tag says so.
Approve retry applies only to that exact action
and the retry still undergoes automatic review; this override has no persistent
or session-wide option.

In a channel, only the person who started the task sees the approval prompt.
In a direct message, it appears in that conversation. Only the original
requester can decide, and they must still be authorized to use Tag there.

If Tag says the request has expired or was already decided, the old buttons
cannot approve it. Review the task's latest status before starting another
request. Unsupported or unavailable approval requests are refused rather than
approved automatically by Tag.

## Respond to a Claude approval request

Claude runs in its `auto` permission mode by default, so most actions proceed
without a prompt. When Claude would still ask, Tag privately shows **Claude
needs approval** with a short row: **Allow once**, the broadest saved rule on
offer, and **Deny**. **More options** shows every choice, including **Deny and
stop**, with the exact rule beside each button. Codex approvals use the same
layout. When Claude proposes a rule or a folder for the action, such as
`Bash(claude --chrome --version)` or a folder outside the workspace, Tag also
offers:

- **Allow for this task** — applies the rule or folder access until this Slack
  request ends.
- **Always allow** — saves it for future requests to this Tag.
- **Always allow this prefix** — for a plain shell command with a subcommand,
  saves a rule for the program and its first argument, such as
  `Bash(claude --chrome:*)`, like Codex's prefix rules.

Saved choices ask for confirmation, then go to this Tag's workspace
`.claude/settings.local.json`. Other Tags and the operator's own Claude
settings are not changed. Remove the line from that file to revoke it.

Claude has no **Approve retry** choice. The same requester and expiry rules
apply, and unanswered or unavailable requests are denied.

## Choose what a Tag may do without asking

In Tag.app, open the Tag's **Details** and find **Permissions**. Codex and
Claude work differently, so each keeps its own settings.

Each choice uses the agent's own name for it.

- **Codex sandbox** (`sandbox_mode`) — **Read only**, **Workspace write**
  (default: changes files only in its folder), or **Full access** (no
  sandbox).
- **Codex approval policy** (`approval_policy`) — **Default** (whatever your
  own Codex config says, on request unless you changed it), **Untrusted**
  (asks before commands that aren't known to be safe), **On request** (Codex
  decides when to ask, mostly to go beyond the sandbox), or **Never**.
- **Codex approvals reviewer** (`approvals_reviewer`) — **Auto review**
  (default; Codex's reviewer decides, and you can retry what it denies) or
  **User**: you, privately in Slack. Hidden when the approval policy is
  Never.
- **Claude mode** (`--permission-mode`) — **Default** (asks before file
  changes and commands), **Accept edits**, **Auto** (Tag's default), **Don't
  ask**, or **Bypass permissions** (skips approvals; only deny rules and hooks
  can stop it).
- **Claude Chrome** (`--chrome`) — **Off** (default) or **On**: Claude uses
  Claude in Chrome in the signed-in Chrome on the computer running Tag. Anyone
  who can ask the Tag can then ask it to act on sites signed in there, so Tag
  marks On as a warning. See
  [Use your signed-in Chrome](adding-integrations.md#use-your-signed-in-chrome).

A running Tag restarts to use the change. From the terminal, run
`tag NAME settings ai permissions codex-sandbox=read-only claude-mode=default --restart`.
See the [backend reference](../../references/backends.md) for the settings
behind each choice.

Only the agent the Tag's model runs on is shown, since every Slack request
uses that model. Switch the model to a Claude one and Claude's settings appear
instead; each agent keeps its own. **Rules** opens the agent's rules.

### Rules

Rules allow or block one command or tool, whatever the mode. Each Tag follows
four sets, which add up:

| Rules | Where you change them |
| --- | --- |
| For this Tag | The Tag's **Details → Permissions → Rules**. **Always allow** in Slack adds here too. |
| In its folder | Other rule files in the Tag's folder: `.codex/rules/*.rules` and `.claude/settings.json`. The agent reads them too; Tag shows them but doesn't change them. |
| For all Tags | **Settings → AI connections → Rules for all Tags** |
| Your own | Your own Codex (`~/.codex/rules`) and Claude Code (`~/.claude/settings.json`) rules. Tag shows them but never changes them, since they also apply when you use Codex or Claude Code yourself. |

To add one, choose **Block** or **Allow**, type a Codex command start
(`git push`) or a Claude rule (`Bash(git push:*)`), and click **Add**.
**Remove** takes one away. A change applies from the next request, with no
restart. A block anywhere wins over an allow anywhere, in every mode, Bypass
permissions and Full access included. So "Bypass permissions, but never
`git push`" is a Block rule.

From the terminal:

```sh
tag NAME settings ai permissions block codex 'git push'      # this Tag
tag NAME settings ai permissions remove codex 'git push'
tag settings ai rules allow claude 'WebFetch(domain:github.com)'  # all Tags
tag settings ai rules                                         # list them
```

A Tag's rules live in its folder: `.codex/rules/tag.rules` and
`.claude/settings.local.json`. Codex reads them only from version 0.162. With
an older Codex, a Tag whose Codex rules block or ask (its own, for all Tags, or
other files in its folder) stops with a message to update Codex rather than run
without them. **Rules** also
lists other rule files in the Tag's folder, which the agent reads too.

The agent's own tools don't change its rules: Codex's sandbox keeps `.codex`
read-only (except with Full access), and Tag blocks Claude's file edits to
`.claude/settings.local.json` and `.claude/settings.json`. With Full access or Bypass
permissions, a shell command could still change them. If one of Claude's settings files can't be read, the
Tag stops with a message rather than run without its blocks.

Codex rules need the Tag's own folder. If a Tag works in a folder of yours
(`OPENTAG_WORKDIR`), Tag keeps no Codex rules there, since they would also
change your own Codex in that folder: it doesn't add or remove them, and the Tag
won't run Codex while the Codex rules for all Tags block or ask about something,
or can't be read. It also won't run Codex there while the folder's own Codex rules
block or ask and your Codex doesn't trust the folder, since Codex would skip them.

## Follow a task as it works

With Codex or Claude, **Agent activity** shows readable tool steps in the Slack
thread, such as reading a file or reviewing changes. Repeated steps are grouped,
and the card shows complete when the task succeeds. See the
[live activity example](../reference/supported-capabilities.md#watch-tag-work).
Short activity descriptions are shared with the thread; full tool inputs and
results are not shown. The separate Activity button in Slack is currently hidden.

On the computer running Tag, the Tag's **Activity** tab in the app keeps a
summary of each reply, grouped by Slack thread, with its saved steps. From the
terminal, `tag logs --json` lists the same records, and
`tag logs --activity RUN_ID` shows one request's saved steps.

## Stop a task

Use Slack's **Stop** control to interrupt an active Codex or Claude task.
Stopping a task does not undo files it already changed or actions it already
completed. Check the result before asking Tag to try again.

```mermaid
flowchart LR
    Ask["You mention<br/>your Tag"] --> Work["Agent activity<br/>in the thread"]
    Work -->|needs a decision| Approve["Private approval<br/>prompt for you"]
    Approve --> Work
    Work -->|Stop| Stopped["Stopped"]
    Work --> Done["Answer in<br/>the thread"]
    Work -->|fails| Failed["Private failure<br/>message with Retry"]
```

Approval buttons, live activity, and Stop require Codex App Server or the Claude
Agent SDK, Tag's default connections. They are not available with the legacy
Codex exec or Claude print transports.

If a request fails, only you see the failure message. It explains what went
wrong and offers **Retry**, **Fix with coding agent**, which copies a short
handoff for a coding agent, and **Report issue**, which helps you copy an error
report to review before sharing. See [Error reports](../reference/error-reporting.md).

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
