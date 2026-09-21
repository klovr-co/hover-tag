<!-- Modified by klovr.co in 2026 for Tag. See NOTICE and repository history. -->

# Slack Adapter

Use this reference when setting up the Slack-facing side of Tag from
scratch. The bridge is intentionally thin. It only:

1. Receives `app_mention` and `message.im` events through Socket Mode.
2. Reads the current thread through Slack Web API.
3. Starts Slack's native working indicator, falling back to a temporary reply
   when that API is unavailable.
4. Runs `scripts/opentag_agent.py` with the selected CLI backend.
5. Optionally streams normalized answer deltas, or posts the final answer when
   the selected backend provides only a completed response.

The adapter does not answer task questions or classify their meaning. It passes
the thread, current-channel MFS scope, and a separate cross-channel search grant
to a fresh CLI agent. The runtime agent understands the request and chooses
whether to call the dedicated Slack-history helper.

By default, `scripts/mfs_search.py` remains narrowed to the invoking channel
exactly as before. Independently, `scripts/slack_search_scope.py` computes the
intersection of the installation workspace, `SLACK_CHANNEL_IDS`, indexed
channel scopes, and live caller visibility. Both Codex and Claude receive that
same pre-authorized result through `scripts/slack_history_search.py`. With no
`--channel` argument the helper searches the entire grant; repeated `--channel`
arguments select exact names from it. Missing, malformed, ambiguous, or
ungranted names fail without searching. Authorization policy is enforced by
code and must not be reimplemented in a prompt or adapter.

The Slack app token opens Socket Mode for receiving invocations. The bot token
authenticates the bot, reads current threads, checks app and workspace identity
and caller visibility for authorized cross-channel search, and posts replies.
The local backend inherits the bot token; Tag withholds the app token and bridge
access-control settings as described in [Security](../SECURITY.md#credential-boundary).
`tag setup` separately configures an MFS Slack-history credential, explicit
channel-ID allowlist, and source URI.

Relevant Slack docs:

- Socket Mode: <https://docs.slack.dev/apis/events-api/using-socket-mode/>
- App mentions: <https://docs.slack.dev/reference/events/app_mention/>
- Direct messages: <https://docs.slack.dev/reference/events/message.im/>
- OAuth scopes: <https://docs.slack.dev/reference/scopes/>

## Prerequisites

MFS must be available for the completed service. Slack setup creates its
connector configuration; `tag start` registers it after starting MFS:

1. `uv tool install mfs-server` → `mfs-server run` (binds `127.0.0.1:13619`;
   verify with `curl -s 127.0.0.1:13619/healthz`).
2. Use **mfs-ingest** only for optional sources beyond the Slack channels chosen
   during `tag setup`.

## End-To-End Checklist

1. Pick one or more isolated Slack channels for the first run and invite the bot.
2. Run `tag setup`. Let it reuse Slack CLI authorization or start `slack auth
   login` when the workspace is absent.
3. Approve either manifest-based app creation or linking an existing App ID.
   Review/repair missing settings in Slack; setup never changes an existing
   app's permissions silently.
4. Enter Socket Mode and bot tokens in the hidden prompts. Setup validates them
   independently and checks workspace/app identity.
5. Select one or more joined channels and the owner member ID.
6. Choose/reuse the history credential and approve the exact channel list and
   history window before connector creation or indexing.
7. Choose Codex (default) or experimental Claude, then run `tag start`.
8. Mention the bot in each selected test channel and record an observed reply;
   service readiness alone is not an end-to-end pass.
9. Send Tag (or the customized app name) a direct message and confirm an
   authorized caller receives a threaded reply without an `@mention`.

If the workspace blocks app creation or install approval, the user must ask a
Slack workspace admin to approve the app. The skill can guide the setup and
diagnose failures, but it cannot bypass workspace policy.

Tag stores each selected channel's stable ID in `SLACK_CHANNEL_IDS` and verifies
bot membership first. Raw IDs remain available for automation with
`tag config set SLACK_CHANNEL_IDS C123,C456`.

## Slack App Setup

Create or reuse a Slack app:
Profile-picture selection and upload require Slack CLI 4.7 or newer.

1. Go to <https://api.slack.com/apps>.
2. Create a new app from scratch in the target workspace. The default name is
   the authenticated operator's first name plus **Tag** and uses a uniquely
   curated Tag waterdrop identity; setup can customize both before creation.
   The name is cosmetic—Tag strips the mention before invoking the configured
   backend.
3. Open **Socket Mode**, enable it, and create an app-level token with:
   - `connections:write`
4. Open **OAuth & Permissions** and add Bot Token Scopes:
   - `app_mentions:read` — receive bot mention events.
   - `assistant:write` — manage native agent working status.
   - `chat:write` — post and update Slack replies.
   - `files:read` — download text snippets and image attachments shared in the current thread.
   - `files:write` — upload explicitly requested generated files, including images, and return private links in the current thread. Requested output files also receive separate **Open filename** actions that validate the requesting user and workspace path before opening the file on the Tag host.
   - `channels:read` + `channels:history` — read threads in public channels.
   - `groups:read` + `groups:history` — read threads in private channels.
   - `im:history` — read direct-message threads when DM invocation is enabled.
   - `users:read` — verify app identity and caller visibility for cross-channel search.
5. Open **Event Subscriptions** and subscribe to Bot Events:
   - `app_mention`
   - `message.im`
   - `app_home_opened`
   - `agent_session_stopped`
6. Start Tag. It applies pending manifest migrations and, when a new scope is
   required, opens Slack's reinstall approval flow automatically.
7. Copy the **Bot User OAuth Token** (`xoxb-...`).
8. Invite the bot to the sandbox channel:
   ```text
   /invite @your-bot-name
   ```
9. Open the app's Home tab to confirm that its visual channel picker shows the
   selected destination. Only configured Tag owners can change it there.

For a private channel, bot membership matters even when the app has
`groups:history`. If `opentag_doctor.py` reports `not_in_channel`, invite the bot
again or use a channel where the bot is present.

## MFS Memory Setup

Use MFS to expose external context as Memory. Keep the allowed scope narrow for a
Slack demo.

Minimal local workspace source:

```bash
mfs add /path/to/workspace
export MFS_ALLOWED_SCOPES="file://local/path/to/workspace"
```

Slack history source:

```toml
# /tmp/opentag-slack.toml
token = "env:SLACK_MEMORY_TOKEN"
channel_types = ["public_channel", "private_channel"]
channel_names = ["team-demo-channel"]
include_unjoined = true
oldest = "now-30d"
max_read_rows = 50000
```

```bash
export SLACK_MEMORY_TOKEN="xoxb-or-xoxp-..."
mfs add slack://team-memory --config /tmp/opentag-slack.toml
export MFS_ALLOWED_SCOPES="slack://team-memory,file://local/path/to/workspace"
```

### More sources

Tag's reach is whatever MFS has indexed plus what you list in
`MFS_ALLOWED_SCOPES`. Add each once with **mfs-ingest** (it handles credentials),
then append its root to the scope list:

```bash
mfs add github://your-org/your-repo --config ./github.toml   # code + issues
mfs add linear://your-workspace     --config ./linear.toml   # issues
mfs add postgres://prod             --config ./pg.toml        # rows as objects
export MFS_ALLOWED_SCOPES="slack://team-memory,github://your-org/your-repo,linear://your-workspace,file://local/path/to/workspace"
```

Do not hand-write Tag's primary Slack connector TOML; setup creates it with
`channel_ids` and a bounded history window. For other connector types, use the
**mfs-ingest** skill and `docs/connectors/`.

Use a bot token for channels the bot can join. Use a user token only when the
demo intentionally needs the user's own visible Slack context, and always pair it
with `channel_ids` or `channel_names` to avoid indexing the entire workspace.

## Environment

Required:

```bash
export SLACK_APP_TOKEN="xapp-..."
export SLACK_BOT_TOKEN="xoxb-..."
export MFS_URL="http://127.0.0.1:13619"
export MFS_TOKEN="$(cat ~/.mfs/server.token)"
export MFS_ALLOWED_SCOPES="slack://team-memory,github://owner/repo,file://local/path/to/workspace"
export OPENTAG_BACKEND="<backend>"   # claude | codex
export OPENTAG_WORKDIR="/path/to/workspace"
export SLACK_CHANNEL_IDS="<channel-id>,<another-channel-id>"
export SLACK_ALLOWED_USER_IDS="<owner-member-id>"
export OPENTAG_SLACK_DM_ENABLED=1
```

`SLACK_ALLOWED_USER_IDS` is required and fails closed when empty. In Slack, open
your profile, choose **More**, then **Copy member ID**. Setup writes that one ID
as the owner-only default; append comma-separated member IDs only when the owner
intentionally shares access. Unauthorized mentions receive a denial without
reading the thread or invoking the backend. Existing installations must add this
setting before restarting Tag.

Direct-message invocation is enabled by default for the same authorized users,
who can invoke Tag without an `@mention` from Tag's Messages tab. The channel
allowlist does not apply to DMs, but `SLACK_ALLOWED_USER_IDS` still does. Set
`OPENTAG_SLACK_DM_ENABLED=0` to disable DM invocation. Each top-level DM starts a
fresh backend task, while replies reuse only that DM thread's bounded context
(up to 30 messages).

The bridge does not need a model API key. The selected CLI backend handles model
auth and tool execution.

### Cross-channel failure behavior

- Slack user or channel lookup failure denies the affected expansion.
- Private channels require caller membership. Restricted and guest users require
  membership for public channels too; pagination is followed to a definitive
  result.
- Shared, archived, deleted, unindexed, unapproved, or wrong-workspace channels
  never enter the backend scope.
- An unavailable requested name is reported generically. Suggestions are drawn
  only from channels whose access was already proven.
- MFS read or search failures remain ordinary retrieval failures and never cause
  a retry with a wider scope.
- Channel names in results are refreshed display metadata. IDs embedded in MFS
  scopes remain the authorization key across renames.

Optional:

```bash
export OPENTAG_TIMEOUT_SECONDS=420      # stop after this much backend inactivity
export OPENTAG_MAX_TIMEOUT_SECONDS=3600 # absolute task limit, even with activity
export OPENTAG_BACKEND_ATTEMPTS=3   # codex backend: retries on capacity/rate-limit
export OPENTAG_SLACK_STREAMING=0    # optional: disable default Slack response streaming
export OPENTAG_CODEX_TRANSPORT=exec # optional legacy rollback; App Server is the default
export OPENTAG_CODEX_MODELS=""      # optional comma-separated model allowlist
export OPENTAG_CODEX_REASONING_EFFORTS="low,medium,high,xhigh,max,ultra"
```

The native Slack loading indicator and response streaming are enabled by
default. Claude provides live answer deltas. Codex defaults to App Server for
phased final-answer deltas, observed activity, and interruption; set
`OPENTAG_CODEX_TRANSPORT=exec` only for legacy rollback. Tag does not
forward commentary, reasoning, tool output, or raw backend diagnostics.

Codex retries capacity and rate-limit failures up to
`OPENTAG_BACKEND_ATTEMPTS`, but only when the failed attempt produced no
observable activity or answer content. Slack displays the next attempt (for
example, `Backend busy — retrying (2/3)…`). After a terminal failure, Tag clears
the working state, keeps detailed diagnostics in local logs under a short error
reference, and posts sanitized guidance with a **Retry** button. Retry reloads
the canonical original message from Slack by timestamp; prompt text is not
stored in the button payload. User-requested stops are terminal and do not show
the Retry button.

The bridge records active Slack session identities in
`state/slack-active-sessions.json` under Tag home. The journal contains only the
workspace, channel, and thread identifiers needed for cleanup. SIGTERM and
SIGINT request a graceful bridge shutdown, which closes recorded sessions before
exit. If the process cannot run cleanup (for example, after SIGKILL or a host
crash), the next bridge start reconciles the journal through both the Agent
Sessions and legacy assistant-status APIs before connecting Socket Mode. Entries
are retained when both cleanup calls fail so a later start can retry them.
If Slack delivers `agent_session_stopped` after the original backend process is
already gone, the bridge treats it as an orphaned stop: it closes the Slack
session immediately instead of waiting for a nonexistent run to confirm
interruption. Failed orphan cleanup is added to the same recovery journal.

Activity copy uses a fixed public vocabulary. Recognized MCP services and tool
names produce natural copy (for example, `Searching GitHub issues…`); unrecognized
names fall back to a service-only or generic label. Extend `MCP_SERVICE_NAMES`
and `MCP_TOOL_NAMES` in `scripts/codex_app_server.py` to approve more display
names. Arguments, results, URLs, and private server identifiers are not used in
activity labels. Helper commands are classified by simple invocation, never by
a filename mentioned somewhere in a command. Compound or ambiguous commands
use generic copy. These labels describe observed attempts, not verified success.
Concurrent work shows the latest activity and the number of other active tools;
updates are coalesced without postponing the display indefinitely during bursts.
Labels change at most every 1.5 seconds during activity updates, grouping rapid
operations. An operation still active after 12 seconds shows `Still waiting for
GitHub…` for a recognized service, or generic ongoing-operation copy. Completion
removes its wait state. `Preparing your answer…` requires an explicit backend
`final_answer` message-start event and no active tools; tool completion alone
does not trigger it. Answer streaming and cleanup cancel pending status updates.
This filtering applies to activity labels, not redaction of the final answer.

For the Codex backend, completed replies include a compact **Configure** button
beneath the answer. It opens a user-scoped settings modal; saved choices apply
to that user's future requests across channels and threads and survive bridge
restarts in `.runtime/`. By default, Tag reads visible models and
their supported reasoning levels and Fast Mode availability from Codex's local
model cache. The modal layers Tag's `workspace/.codex/config.toml` model,
reasoning, and service-tier defaults over the corresponding global Codex
settings. Restart Tag after editing the local file. Reasoning levels retain
Codex's native names. Fast Mode is a
separate On/Off setting and uses increased usage when enabled. Set
`OPENTAG_CODEX_MODELS` to restrict what Slack users can select.
The modal's **Reset to default** button restores every control before saving.
When upgrading an existing app, run `tag start` in an interactive terminal.
Tag merges pending required scopes and event subscriptions into the remote
manifest without removing operator-owned settings. Slack may still require an
owner or workspace admin to approve newly requested OAuth permissions.

## Preflight

Run this before starting the bridge:

```bash
python scripts/opentag_doctor.py --channel-id "<first-id>" --channel-id "<second-id>"
```

The doctor checks:

- required environment variables are present;
- at least one allowed Slack caller is configured;
- Slack bot token authenticates;
- the bot can see the target channel and read its history;
- MFS is reachable and each allowed scope can be listed;
- the selected backend is available.

Do not proceed until all checks pass. A failed MFS scope usually means the source
is not registered, not indexed yet, or the URI does not match the registered
connector root. A failed Slack channel check usually means missing scopes, app
not reinstalled after scope changes, or bot not invited to the channel.

## Run

From the skill directory:

```bash
uv run --with slack-bolt python scripts/slack_socket_agent.py --backend "$OPENTAG_BACKEND"
```

Then mention the bot in Slack:

```text
@your-bot-name Review the recent customer-facing discussion about the product issue, compare it with the repo docs, draft a short follow-up memo in the workspace, and reply with the file path plus open questions.
```

Follow-up messages in the same Slack thread are passed to the next backend run
through `conversations.replies`.

By default, an authorized user can instead open Tag's Messages tab and send
a top-level request without mentioning the bot. Use thread replies for
follow-ups; send a new top-level DM to begin a separate task.

## Manual Non-Slack Test

Use this before the real Slack run if backend behavior is uncertain:

```bash
cat >/tmp/opentag-thread.txt <<'EOF'
U123: <@BOT> Review the recent customer-facing discussion about a product issue, compare it with the repository docs, draft a short follow-up memo in the workspace, and reply with the file path plus open questions.
EOF

python scripts/opentag_agent.py \
  --backend "$OPENTAG_BACKEND" \
  --channel-id "$SLACK_CHANNEL_ID" \
  --question "Review the recent customer-facing discussion about a product issue, compare it with the repository docs, draft a short follow-up memo in the workspace, and reply with the file path plus open questions." \
  --thread-file /tmp/opentag-thread.txt \
  --workdir /path/to/workspace
```

## Permission Notes

- Invite the Slack app only to channels where the bridge should respond.
- Keep broad Slack history access in MFS connector config, not in the Slack
  bridge. Use channel IDs or channel names to allowlist what the connector
  indexes.
- Treat `MFS_ALLOWED_SCOPES` as the runtime memory boundary. The helper scripts
  reject reads and searches outside those scopes.
- Use a sandbox Slack channel and a non-production workspace when testing
  backend command execution.
