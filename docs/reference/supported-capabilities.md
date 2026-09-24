# Supported capabilities

This page separates what Tag implements today from optional, experimental, or
unavailable behavior.

## Slack experience

| Capability | Status | Notes |
| --- | --- | --- |
| Respond to app mentions | Implemented | The caller must be in `SLACK_ALLOWED_USER_IDS`. |
| Read the current thread | Implemented | Tag fetches one page containing up to 30 messages. |
| Read supported attachments | Implemented | Text content is truncated at 12,000 characters per item; downloaded image or text files are limited to 15 MiB. |
| Stream answer text | Backend-dependent | Claude streams text deltas; Codex App Server streams final-answer deltas and observed activity. |
| Continue with thread context | Implemented | A later mention receives the current bounded thread context. |
| Post a requested top-level message | Implemented | Restricted to the channel that invoked Tag. |
| Create a requested Slack Canvas | Implemented | Requires the Slack Canvas scope and explicit user intent. |
| Upload generated images as results | Implemented | Uploads supported backend-generated PNG, JPEG, GIF, and WebP results to the requesting thread; requires `files:write`. Image generation depends on the backend's available tools. |

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
| Organization-wide administration and approvals | Not provided | These remain outside the current reference implementation. |

For the full end-to-end behavior, see
[Connected user flows](../user-flows.md). For configuration and exact backend
commands, use the [Slack adapter](../../references/slack-adapter.md) and
[backend reference](../../references/backends.md).
