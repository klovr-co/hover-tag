# Supported capabilities

This page separates what Tag implements today from optional, experimental, or
unavailable behavior.

## Slack experience

| Capability | Status | Notes |
| --- | --- | --- |
| Respond to app mentions | Implemented | The caller must be in `SLACK_ALLOWED_USER_IDS`. |
| Read the current thread | Implemented | Tag fetches one page containing up to 30 messages. |
| Read supported attachments | Implemented | Includes [forwarded Slack files](../concepts/workspaces-and-tools.md#use-a-forwarded-slack-file). Text content is truncated at 12,000 characters per item; downloaded image or text files are limited to 15 MiB. |
| Stream answer text | Implemented | Codex App Server and the Claude Agent SDK stream only final-answer text; commentary and reasoning stay private. |
| Watch live activity | Implemented with App Server or Agent SDK | Readable tool steps appear in the Slack thread while the agent works. See [Watch Tag work](#watch-tag-work). |
| Continue with thread context | Implemented | A later mention receives the current bounded thread context. |
| Post a requested top-level message | Implemented | Restricted to the channel that invoked Tag. |
| Create a requested Slack Canvas | Implemented | Requires the Slack Canvas scope and explicit user intent. |
| Deliver saved files | Implemented | Keeps local copies and uploads Slack attachments by default. One Open folder action remains; individual file actions appear for local-only files and failed or oversized uploads. See [Working with files](../concepts/workspaces-and-tools.md#also-attach-saved-files-in-slack). |
| Upload generated images as results | Implemented | Uploads supported backend-generated PNG, JPEG, GIF, and WebP results to the requesting thread; requires `files:write`. Image generation depends on the backend's available tools. |
| Recover from failed requests | Implemented | Private failure messages offer retry, a coding-agent handoff, and a manually shared [error report](error-reporting.md). |

## Agent work

| Capability | Status | Notes |
| --- | --- | --- |
| Codex CLI backend | Supported | Used by the v0.1 launch qualification. |
| Show model and duration | Implemented | Finished replies show the agent, the model the agent reports it actually used (including when the account default was used), thinking level, Fast Mode, and how long the request took, for example `Claude · Opus 5.5 · high thinking · 1m 12s`. The legacy Codex exec and Claude print transports do not report a model, so their replies show the chosen model instead. |
| Switch models between backends | Implemented | Configure lists models from every signed-in backend; the chosen model selects Codex or Claude for that user's next request. `OPENTAG_DEFAULT_MODEL` sets the Tag default. |
| Claude Code backend | Supported | Uses the Claude Agent SDK with an authenticated local Claude CLI session; supports streaming, activity, private one-time approvals, Stop, and model settings; approval scope differences are listed below. |
| Inspect and change workspace files | Implemented | Uses the permissions of the backend process. |
| Run workspace commands and tests | Implemented | Available when the selected backend can perform them. |
| Use installed local tools and skills | Available | Each tool uses its own credentials and grants. |
| Choose Codex model, reasoning, and Fast Mode per user | Implemented | Saved choices follow the user across channels and threads; operators can restrict model and reasoning choices. |
| Stop an active Codex turn | Implemented with App Server | Slack's native Stop button interrupts the active turn; the legacy exec transport remains a rollback path. |

## Context and memory

| Capability | Status | Notes |
| --- | --- | --- |
| Search allowed MFS scopes | Implemented | `MFS_ALLOWED_SCOPES` limits the bundled search helper. |
| Reopen precise MFS records | Implemented | Read and list helpers enforce the configured roots. |
| Use indexed Slack channel history | Implemented | Requires a configured MFS Slack connector and an allowed `slack://` root. |
| Use repositories, documents, issues, databases, and object stores | Connector-dependent | The source must already be indexed by MFS and permitted to Tag. |
| Automatically remember every conversation | Not provided | Continuity comes from Slack threads, workspace state, and approved indexed sources. |
| Dedicated local memory-note store | Not provided | Durable retrieval uses indexed, permitted MFS sources. |

## Operator controls

| Capability | Status | Notes |
| --- | --- | --- |
| Owner access | Supported | Configure the owner's Slack member ID during setup. |
| Multi-user access | Coming soon | Additional callers are not part of the supported product flow yet, although the underlying allowlist accepts multiple IDs. |
| Explicit channel restriction | Implemented | Configure `SLACK_CHANNEL_IDS`; the bridge fails closed without selected channels. |
| MFS retrieval roots | Implemented | Configure `MFS_ALLOWED_SCOPES`. |
| Backend timeout and retry settings | Implemented | Configure the corresponding `OPENTAG_` settings. |
| Codex action approvals | Implemented fallback | Codex normally reviews sandbox-boundary actions automatically. Supported requests show private native choices, including one-time, task-scoped, and proposed persistent-rule decisions. Auto-review denials offer **Approve retry** / **Dismiss**. See [Control your Tag](../concepts/control-your-tag.md#respond-to-a-codex-approval-request). |
| Claude action approvals | One-time decisions | SDK permission requests offer private Approve / Deny controls. The Claude adapter does not expose task-scoped grants, persistent-rule choices, or automatic-review denial retries; missing approval channels and unanswered requests are denied. |
| Organization-wide administration and approvals | Not provided | These remain outside the current reference implementation. |

## Respond to a Codex approval request

For the approval walkthrough, example message, and Stop controls, see
[Control your Tag](../concepts/control-your-tag.md).

## Watch Tag work

You can follow a Codex task directly in its Slack thread. Tag starts with a
working indicator, then shows **Agent activity** as tools run: reading a file,
running a script, reviewing changes, or updating a document. This is the visible
progress of the task; Tag does not show private reasoning.

Here is an illustrative file task. On the website, the final assistant message
moves from the working indicator through two tool steps to the answer.

> **Tag live activity**
>
> **Maya:** @Tag read launch-plan.md and create launch-checklist.md.
>
> | In progress | Completed |
> | --- | --- |
> | Reading launch-plan.md | Read launch-plan.md |
> | Creating launch-checklist.md | Created launch-checklist.md |
>
> **Maya's Tag:** Created launch-checklist.md with the launch tasks and owners from launch-plan.md.

Steps use short command or file descriptions instead of raw tool payloads.
Repeated work is grouped with a count, keeping the activity compact. The latest
action stays visible between tool calls; Tag does not add a new “thinking” row
every time it waits.

When the task succeeds, **Agent activity** shows complete and the answer appears
in the same message. A failed or stopped task keeps its failure or interruption
state. Completion is a task status, not proof that every intermediate action
succeeded; review the final answer and any error message for the outcome.

Everyone who can see the thread can see these short activity descriptions,
including file names. Full tool inputs and results are not shown in the thread.
The separate **Activity** button is currently hidden; a developer view may return
in a future release. Live tool activity requires Codex App Server or the
Claude Agent SDK; the legacy Codex exec and Claude print transports do not show
these tool rows.

For the full end-to-end behavior, see
[Connected user flows](../user-flows.md). For configuration and exact backend
commands, use the [Slack adapter](../../references/slack-adapter.md) and
[backend reference](../../references/backends.md).
