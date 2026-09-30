# Supported capabilities

This page separates what Tag implements today from optional, experimental, or
unavailable behavior.

## Slack experience

| Capability | Status | Notes |
| --- | --- | --- |
| Respond to app mentions | Implemented | The caller must be in `SLACK_ALLOWED_USER_IDS`. |
| Read the current thread | Implemented | Tag fetches one page containing up to 30 messages. |
| Read supported attachments | Implemented | Includes [forwarded Slack files](../concepts/workspaces-and-tools.md#use-a-forwarded-slack-file). Text content is truncated at 12,000 characters per item; downloaded image or text files are limited to 15 MiB. |
| Stream answer text | Backend-dependent | Claude streams text deltas; Codex App Server streams final-answer deltas and observed activity. |
| Review task activity | Implemented with App Server | Codex App Server replies include a requester-only Activity timeline with bounded, redacted tool input and result previews. See [Review task activity](#review-task-activity). |
| Continue with thread context | Implemented | A later mention receives the current bounded thread context. |
| Post a requested top-level message | Implemented | Restricted to the channel that invoked Tag. |
| Create a requested Slack Canvas | Implemented | Requires the Slack Canvas scope and explicit user intent. |
| Deliver saved files | Implemented | Keeps local copies and uploads Slack attachments by default. One Open folder action remains; individual file actions appear for local-only files and failed or oversized uploads. See [Working with files](../concepts/workspaces-and-tools.md#also-attach-saved-files-in-slack). |
| Upload generated images as results | Implemented | Uploads supported backend-generated PNG, JPEG, GIF, and WebP results to the requesting thread; requires `files:write`. Image generation depends on the backend's available tools. |
| Recover from failed requests | Implemented | Private failure messages offer retry, a coding-agent handoff, and a manually shared [error report](error-reporting.md). |

## Agent work

| Capability | Status | Notes |
| --- | --- | --- |
| Codex CLI backend | Supported path | Used by the v0.1 launch qualification. |
| Claude Code backend | Experimental | Requires an authenticated local Claude CLI session. |
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
| Codex action approvals | Implemented fallback | Codex normally reviews sandbox-boundary actions automatically. Supported requests delivered to Tag show private **Approve once** and **Deny** buttons for the initiating user. See [Control your Tag](../concepts/control-your-tag.md#respond-to-a-codex-approval-request). |
| Organization-wide administration and approvals | Not provided | These remain outside the current reference implementation. |

## Respond to a Codex approval request

For the approval walkthrough, example message, and Stop controls, see
[Control your Tag](../concepts/control-your-tag.md).

## Review task activity

After a Codex task finishes, select **Activity** on Tag's reply to see the tools
it used. Successful replies show **Activity** beside **Configure**; failed and
stopped tasks show **Activity** alongside their recovery actions.

1. Open **Activity** to see a timeline of tool steps, timestamps, and status.
2. Select **Details** on a step to inspect its available input and result previews.
3. Select **Back** to return to the timeline.

The example below illustrates a finished task with two observed tool steps.
Select **Details** to explore a preview.

> **Tag activity**
>
> Finished · 2 observed tool steps
>
> | Time | Step | Status | Input | Result |
> | --- | --- | --- | --- | --- |
> | 13:07:05 UTC | Reading files… | Tool finished | Read launch-plan.md | Launch: Monday. FAQ owner: Maya. Signup testing: Jules. |
> | 13:07:22 UTC | Writing files… | Tool finished | Create launch-checklist.md from the launch plan | Created launch-checklist.md with the launch tasks and owners. |
>
> Tool completion alone does not confirm an external action's outcome.

Only the person who submitted the task can open its activity, and they must
still have access to use Tag in that conversation. The previews open in a
private Slack modal.

Tag shortens previews and redacts common credential patterns and secret-named
fields. Prompts, commentary, and private reasoning are excluded. Activity is
available for up to 30 days; older records may be removed sooner as new tasks
reach the storage limit.

A completed step means the tool call ended; check the result to confirm whether
the requested action succeeded. Calls made inside another tool step may not
appear separately. Activity requires Codex App Server, Tag's default Codex
connection; it is unavailable with the legacy Codex exec or Claude backends.

For the full end-to-end behavior, see
[Connected user flows](../user-flows.md). For configuration and exact backend
commands, use the [Slack adapter](../../references/slack-adapter.md) and
[backend reference](../../references/backends.md).
