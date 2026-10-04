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
| Inspect saved activity | Implemented with App Server or Agent SDK | Tag.app's Activity feed shows a cached AI-written, one-sentence summary of new delivered replies, generated automatically by either backend after delivery. A local excerpt remains while pending or if generation fails. Summaries wrap in full; each entry shows its model, saved thinking level, elapsed backend time, and reported token usage for Codex and Claude. Hovering or focusing the token count shows input/output and any reported cache/reasoning subsets. Older thinking levels are never inferred from current settings. Missing usage and missing, zero, or invalid durations are omitted, as are absent tool previews. Finished reply rows appear only when they have a saved summary, excerpt, or artifact. New outputs appear as filename chips with recorded upload status, opening uploaded files in Slack or workspace files locally. Generated-image delivery finishes before summarization; the shared flow works for both backends. Feeds open at the latest request at the bottom of chronological history, load older requests when scrolling up, and preserve the reading position. A compact Show errors checkbox beside the tabs is off by default and can reveal failed requests without changing history. Each channel has an Activity tab combining matching records from the workspace’s Tags, plus a Tags in this channel tab. CLI activity JSON supports `--activity-channel CHANNEL_ID` and `--hide-errors`, filtering before the recent-record limit; `--activity-limit` expands the window and `activity_has_more` reports older retained history. The feed and `tag NAME logs --activity RUN_ID` show retained tool previews and matching error reports. Empty or expired records cannot reconstruct unrecorded details. |
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
| Switch models between backends | Implemented | Tag.app Details and `tag settings ai model` list models from connected backends. `OPENTAG_DEFAULT_MODEL` selects Codex or Claude for all requests to that Tag. Slack per-user overrides are retired. |
| Tag default thinking level | Implemented for both | `OPENTAG_DEFAULT_EFFORT` (or `tag settings ai effort`) sets the default model's thinking level when that model offers it; Codex receives it as the App Server turn `effort` and Claude as the Agent SDK `effort` option. Models without thinking levels, such as Claude Haiku, ignore it. All requesters use the Tag's thinking level. |
| Connect agents in setup and Settings | Implemented for both | Setup and Settings → AI & models detect Codex and Claude Code, show each connection's state, and offer browser sign-in, reconnect, account changes, or install guides. Sign-in reports progress and can be cancelled and retried. One connected agent is required; both are optional. |
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
| Remember facts on request | Implemented | Explicit save, change, and forget for the current channel or, when asked, all channels. Saved facts load before every request in that channel. Codex and Claude use the same store and helper. |
| Report memory changes | Implemented | Verified saves, changes, and forgets are added to the Slack reply from the store's receipts, including when the run fails. |
| Provider-native memory | Disabled | Tag turns off Claude auto memory and Codex memories so that every remembered fact can be listed, corrected, and forgotten through Tag. |
| Automatically remember every conversation | Not provided | Continuity comes from Slack threads, workspace state, saved memory, and approved indexed sources. |

## Operator controls

| Capability | Status | Notes |
| --- | --- | --- |
| Owner access | Supported | Setup makes the person signed in to Slack the owner. |
| Multi-user access | Coming soon | Additional callers are not part of the supported product flow yet, although the underlying allowlist accepts multiple IDs. |
| Explicit channel restriction | Implemented | Configure `SLACK_CHANNEL_IDS`; the bridge fails closed without selected channels. A Tag that follows invitations may start with none and picks up channels it's invited to. |
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

## API connections and usage

| Capability | Codex | Claude |
| --- | --- | --- |
| Explicit per-Tag API key and base URL | Responses-compatible providers | Anthropic-compatible providers via Agent SDK |
| Temporary gateway chat-only mode | Opt-in `OPENTAG_CODEX_GATEWAY_DISABLE_TOOLS=1`; requires provider routing; all tools unavailable | Unsupported; setting is rejected |
| Optional gateway provider pinning | `provider.only` through a task-scoped adapter in API mode; requires explicit base URL | Unsupported; routing settings are rejected |
| Azure OpenAI resource key | Responses endpoint and deployment name | Not supported by this connection mode |
| Custom model list | Operator-declared deployment/model IDs | Operator-declared model IDs |
| Provider-hosted web search through custom endpoints | Disabled for custom API URLs and Azure; local coding tools remain available | SDK tools follow Anthropic gateway support; no Responses configuration applies |
| Hosted image generation and multi-agent tool namespaces through custom endpoints | Disabled for custom API URLs and Azure for function/custom-only gateway compatibility | SDK tools follow Anthropic gateway support; no Responses configuration applies |
| MCP tools through custom endpoints | Code Mode exposes MCP/app tools via custom `exec` and function `wait` tools instead of Responses namespaces; requires Code Mode support | Native SDK MCP transport; no Codex Code Mode configuration applies |
| Local token accounting | Cumulative App Server thread usage | Final SDK result usage |
| Cost estimate | Operator-provided deployment rates | SDK-reported estimate; custom gateway pricing may differ |
| Monthly budget per Tag | Advisory, shared across both backends | Advisory, shared across both backends |
| Hard budget enforcement | Not implemented | Not implemented |

Usage may be incomplete after interruption. API keys and entitlement are not
verified by catalog or readiness checks. Live Azure and Anthropic inference still
require provider qualification. See [configuration and usage details](api-connections.md).
