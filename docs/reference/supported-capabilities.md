# Supported capabilities

This page separates what Tag implements today from optional, experimental, or
unavailable behavior.

## Slack experience

| Capability | Status | Notes |
| --- | --- | --- |
| Respond to app mentions | Implemented | The caller must be in `SLACK_ALLOWED_USER_IDS`, or be another app's bot listed in `SLACK_ALLOWED_BOT_IDS`. |
| Read the current thread | Implemented | Tag fetches one page containing up to 30 messages. |
| Read supported attachments | Implemented | Includes [forwarded Slack files](../concepts/workspaces-and-tools.md#use-a-forwarded-slack-file). Either backend can open [earlier thread files](../concepts/workspaces-and-tools.md#use-earlier-files-in-a-thread) shared in the current channel when a request needs them, up to 15 MB each. Text content is truncated at 12,000 characters per item; downloaded image or text files are limited to 15 MiB. |
| Stream answer text | Implemented | Codex App Server and the Claude Agent SDK stream only final-answer text; commentary and reasoning stay private. |
| Watch live activity | Implemented with App Server or Agent SDK | Readable tool steps appear in the Slack thread while the agent works. See [Watch Tag work](#watch-tag-work). |
| Inspect saved activity | Implemented with App Server or Agent SDK | The app's Activity tab lists delivered replies, newest at the bottom, grouped by Slack thread (**N earlier replies** expands a group). Each entry has an AI-written one-sentence summary (a local excerpt while it's pending or if it fails), the model, saved thinking level, elapsed time, reported token usage, and output file chips. Its step count opens the saved tool steps. **Show errors** reveals failed requests. Each channel also has an Activity tab across the workspace's Tags. In the CLI, `tag NAME logs --json` includes the same records (filter with `--activity-channel`, `--hide-errors` and `--activity-limit`), and `tag NAME logs --activity RUN_ID` shows one run's tool previews and error reports. Expired records can't be rebuilt. |
| Continue with thread context | Implemented | A later mention continues the thread's agent conversation (see below); a fresh conversation receives one page of up to 30 messages. |
| Post a requested top-level message | Implemented | Restricted to the channel that invoked Tag. |
| Ask other Tags and combine their answers | Implemented | Requires `OPENTAG_PEER_TAGS` on each Tag. One request message asks up to five Tags; replies collect in its thread; Tag combines them once all reply or the deadline passes. A Tag answering another Tag cannot pass the work on. Works with Codex and Claude. |
| Create a requested Slack Canvas | Implemented | Requires the Slack Canvas scope and explicit user intent. |
| Deliver saved files | Implemented | Keeps local copies and uploads Slack attachments by default. One Open folder action remains; individual file actions appear for local-only files and failed or oversized uploads. See [Working with files](../concepts/workspaces-and-tools.md#also-attach-saved-files-in-slack). |
| Upload generated images as results | Implemented | Uploads supported backend-generated PNG, JPEG, GIF, and WebP results to the requesting thread and keeps a copy in the channel's `artifacts/<channel>/images` folder; requires `files:write`. Image generation depends on the backend's available tools. |
| Recover from failed requests | Implemented | Private failure messages offer retry, a coding-agent handoff, and a manually shared [error report](error-reporting.md). |

## Agent work

| Capability | Status | Notes |
| --- | --- | --- |
| Codex and Claude backends | Supported | Codex runs through Codex App Server and Claude through the Claude Agent SDK, with the same Slack behavior. Either can use other AI providers through a compatible API or gateway; see [Models](#api-connections-and-usage). Codex was used by the v0.1 launch qualification. |
| Show model and duration | Implemented | Finished replies show the agent, the model the agent reports it actually used (including when the account default was used), thinking level, Fast Mode, and how long the request took, for example `Claude · Opus 5.5 · high thinking · 1m 12s`. The legacy Codex exec and Claude print transports do not report a model, so their replies show the chosen model instead. |
| Switch models between backends | Implemented | The app's Details tab and `tag settings ai model` list models from connected backends. `OPENTAG_DEFAULT_MODEL` selects Codex or Claude for all requests to that Tag. Slack per-user overrides are retired. |
| Tag default thinking level | Implemented for both | `OPENTAG_DEFAULT_EFFORT` (or `tag settings ai effort`) sets the default model's thinking level when that model offers it; Codex receives it as the App Server turn `effort` and Claude as the Agent SDK `effort` option. Models without thinking levels, such as Claude Haiku, ignore it. All requesters use the Tag's thinking level. |
| Connect agents in setup and Settings | Implemented for both | Setup and Settings → General → AI connections detect Codex and Claude Code, show each connection's state, and offer browser sign-in (including a shared ChatGPT plan for Codex), reconnect, account changes, or install guides. Sign-in reports progress and can be cancelled and retried. One connected agent is required; both are optional. |
| Claude Code backend | Supported | Uses the Claude Agent SDK with the local Claude sign-in or an [API connection](api-connections.md); supports streaming, activity, private scoped approvals, Stop, timeouts, and model settings; approval scope differences are listed below. |
| Continue a conversation per Slack thread | Implemented for both | Later mentions in one Slack thread resume the same Codex thread or Claude session, so earlier tool results and reasoning carry forward. Only the requester who started a conversation continues it. A conversation continues until it reaches `OPENTAG_THREAD_MAX_CONTEXT_TOKENS` (default 150000) or sits idle `OPENTAG_THREAD_IDLE_HOURS` (default 4); set either with `tag config set`. Continued requests send only new thread messages, and standing instructions are never repeated in the conversation. Tag titles each conversation with its latest reply summary and groups the thread's replies into one Activity entry in the app. The legacy Codex exec and Claude print transports start a new conversation each time. |
| Background sub-agents | Implemented for both | A request finishes only after its background sub-agents finish. Claude wakes itself when they report back; for Codex, Tag starts the follow-up turn. Interim "I'll report back" text is not posted as the answer. |
| Inspect and change workspace files | Implemented | Uses the permissions of the backend process. |
| Run workspace commands and tests | Implemented | Available when the selected backend can perform them. |
| Use installed local tools and skills | Available | Each tool uses its own credentials and grants. |
| Choose the model and thinking level | Implemented for both | Each Tag has one default model and thinking level, set in the Tag app → Details or `tag settings ai`. Per-user Slack overrides are retired. |
| Stop an active task | Implemented with App Server or Agent SDK | Slack's native Stop button interrupts the active Codex turn or Claude session; the legacy exec and print transports remain rollback paths. |

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
| Codex action approvals | Implemented fallback | Codex normally reviews sandbox-boundary actions automatically. Remaining requests show private choices to the requester, such as **Allow once**, **Allow for this task**, **Deny** and **Deny and stop**, plus proposed persistent rules that need confirmation. Auto-review denials offer **Approve retry** / **Dismiss**. See [Control your Tag](../concepts/control-your-tag.md#respond-to-a-codex-approval-request). |
| Claude action approvals | Implemented | SDK permission requests offer private **Allow once**, **Deny**, and **Deny and stop**. When Claude suggests an allow rule or folder, Tag also offers **Allow for this task** (session scope) and **Always allow**; plain shell commands also offer **Always allow this prefix**. Saved choices go to the Tag workspace's `.claude/settings.local.json` after confirmation. Claude has no automatic-review denial retry; missing approval channels and unanswered requests are denied. |
| Organization-wide administration and approvals | Not provided | These remain outside the current reference implementation. |

## Respond to a Codex approval request

For the approval walkthrough, example message, and Stop controls, see
[Control your Tag](../concepts/control-your-tag.md).

## Watch Tag work

You can follow a Codex or Claude task directly in its Slack thread. Tag starts with a
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
Full steps are in the app's Activity tab, visible only on the computer running
the Tag. Live tool activity requires Codex App Server or the
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
| Set, check, and clear from Tag.app or `tag settings ai api` | OpenAI-compatible and Azure OpenAI | Anthropic-compatible; Azure is rejected |
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
