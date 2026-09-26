# Troubleshooting

Start with `./tag doctor`, then use the first failed check below.

| Failure | What it means | Fix |
| --- | --- | --- |
| `mfs-server` or `mfs` missing/wrong version | The pinned memory runtime is unavailable | Run `./install.sh` again and ensure uv's tool directory and `~/.local/bin` are on `PATH`. |
| MFS health fails | Nothing is listening at `MFS_URL` | Run `./tag start`; inspect `./tag logs` and `~/.mfs/server.log`. |
| Local MFS is healthy but untracked | Another process owns the loopback endpoint | `tag start` and `tag dev` replace an identifiable `mfs-server` with Tag's current runtime. If another kind of service owns the port, stop it or configure a different `MFS_URL`. Remote endpoints are never replaced. |
| MFS status has no connectors | MFS has no indexed source | Add a source with MFS, then include its exact root in `MFS_ALLOWED_SCOPES`. |
| MFS scope fails | The scope is absent, outside policy, or its connector credential is unavailable | Compare the exact URI with `mfs ls`; restart MFS after exporting credentials referenced by connector configuration. |
| Cross-channel search rejects a channel | The requested name is absent or non-unique in the caller's live grant | Check the channel name, bot membership, caller membership for private/guest access, indexing, and Slack connectivity. The runtime helper never searches outside its bridge-generated grant. |
| Slack app token fails | Socket Mode cannot connect | Create an `xapp-` app-level token with `connections:write`. |
| Slack bot token fails | Web API calls cannot authenticate | Reinstall the Slack app and rerun `tag setup`; enter the `xoxb-` token only in its hidden prompt. |
| Slack allowed users fails | No caller is authorized, so the bridge fails closed | Copy the owner's Slack member ID and set it in `SLACK_ALLOWED_USER_IDS`. |
| Slack channel/history fails | The bot is absent or lacks scopes | Invite the bot, choose the channel again in setup, Settings, or App Home, and reinstall after changing manifest scopes. |
| Slack app installation asks for approval | Workspace or Enterprise app approval is enabled | Submit the Slack app request to a workspace owner or app manager; Tag cannot bypass workspace policy. |
| Generated image is described but not attached | The app lacks `files:write`, the result is unsupported or over 15 MB, or the backend did not save it in the prompted result directory | Reinstall the app from the current manifest, retry with PNG/JPEG/GIF/WebP, and inspect Tag logs for the per-file upload error. |
| **Open filename** reports that a local file could not be opened | The file was moved or deleted, its path no longer resolves inside the workspace, or the Tag host has no active desktop application for that file type | Confirm the file still exists in the configured workspace and open it directly on the Tag host to verify its desktop file association. |
| Codex missing | The supported backend is not available | Install/login to Codex CLI and confirm `codex --version` works in the same shell. |
| Bridge immediately stops | Runtime dependency or configuration failed after preflight | Run `./tag logs`; rerun `./scripts/ci_check.sh` before reporting a bug. |
| Mention is denied | The caller is not in the Slack user allowlist | Add their exact member ID to `SLACK_ALLOWED_USER_IDS` only if the owner intends to share access. |
| Mention receives no reply | Slack did not emit an event or the bridge rejected the channel | Confirm Socket Mode is connected, mention your Tag from an authorized human account, and verify the channel is in `SLACK_CHANNEL_IDS`. |
| Direct message receives no reply | DM invocation was disabled, its automatic Slack migration is pending, or the sender is not authorized | Ensure `OPENTAG_SLACK_DM_ENABLED` is not `0`, run `tag restart` in an interactive terminal and approve Slack's permission prompt if shown, then confirm the sender is in `SLACK_ALLOWED_USER_IDS`. |

## Failed Slack requests

When a backend request fails, Tag shows an evidence-based cause, an error
reference, and recovery actions. An unrecognized failure is shown as **Cause
not identified**; a timeout does not by itself mean that the network failed.
Retrying creates a new reference and keeps the earlier attempt linked in the
local report.

**Report issue** opens a private preview for the authorized person who made the
request. The preview contains an allowlisted report with the Tag version,
backend identity when available, failed stage, recognized cause, bounded
diagnostics, and later health-check timestamps. It does not include tokens,
conversation text, file contents, or raw logs by default. Add context only after
reviewing it. Slack does not provide a clipboard button for this surface, so
select the report text manually. Nothing is posted automatically.

To share a report, select the reviewed text, [join Hover Community](https://join.slack.com/t/hover-community/shared_invite/zt-4aghkshid-n7fRukS7_J5sR2jDLBXK9A),
and paste it into the discussion. GitHub Issues remain the canonical record for
confirmed bugs; community posting is a manual first-release handoff. Reports
are stored in the selected Tag's private state for up to 30 days, bounded to 50
records, and an expired or unavailable reference is not treated as authorization
to read anything.

**Fix with coding agent** creates a copyable troubleshooting prompt. Repair
requires an agent with access to the machine running Tag; an agent elsewhere
can analyze the sanitized report but must not claim it inspected local logs or
settings. Tag cannot guarantee that an external agent completes a repair or
reports back. Use the bundled `tag-troubleshoot` skill when it is available;
otherwise the prompt falls back to `tag inspect --json`, `tag doctor --json`,
`tag status --json`, and bounded `tag logs --limit 50` checks.

When reporting a problem, include the Tag version, operating system, Python
version, failing check, and redacted log excerpt. Never include tokens.

## Slack indexing is rate-limited

Tag-managed memory honors Slack's HTTP 429 `Retry-After` delay, shares the
cooldown across channels in the same workspace, and retries the current API
request without restarting the channel's in-progress history read. Channel
listings are cached for up to a minute so readiness checks do not repeatedly
consume Slack's discovery quota.

During a cooldown, startup and `tag status` show **Indexing paused by Slack**
with the retry delay. Indexing continues in the background if the startup
readiness wait expires. Let it finish, then run `tag start`; repeating setup
or restarting memory does not clear Slack's limit. A memory restart preserves
the cooldown, but may require replaying an interrupted indexing task.

Existing Tag-managed memory processes automatically migrate to the rate-aware
runtime on the next start. Tag verifies process identity before restarting
memory and verifies the new runtime and HTTP health before recording the
migration. This briefly interrupts shared memory for other running Tags;
stored indexes, connector configuration, and credentials are preserved.
Independently managed or remote MFS servers require an equivalent connector
update by their operator; Tag does not replace their runtime.
