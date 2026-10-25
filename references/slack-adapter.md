<!-- Modified by klovr.co in 2026 for Tag. See NOTICE and repository history. -->

# Slack Adapter

Use this reference when setting up the Slack-facing side of Tag from
scratch. The bridge is intentionally thin. It only:

1. Receives `app_mention`, channel-message, and `message.im` events through Socket Mode.
2. Reads the current thread through Slack Web API.
3. Starts Slack's native working indicator, falling back to a temporary reply
   when that API is unavailable.
4. Runs `scripts/opentag_agent.py` with the selected CLI backend.
5. Optionally streams normalized answer deltas, or posts the final answer when
   the selected backend provides only a completed response.

The adapter does not answer task questions. Explicit mentions and direct messages
pass the thread, current-channel MFS scope, and a separate cross-channel search
grant to a fresh CLI agent. The runtime agent understands the request and chooses
whether to call the dedicated Slack-history helper. In channels where
watching is turned on, the adapter also decides whether to answer some messages
without an @mention. Those replies have reduced capability; see
[Channel watching](#channel-watching-experimental).

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
- Public channel messages: <https://docs.slack.dev/reference/events/message.channels/>
- Private channel messages: <https://docs.slack.dev/reference/events/message.groups/>
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
2. Run `tag setup`. Name the Tag, describe it in one line, and pick its
   picture; then choose its default model.
3. Pick the workspace from the Slack CLI's sign-ins, or sign in with `slack
   auth login` when it's absent. The signed-in member becomes the owner.
4. Approve the Create recap, or pick an existing app and approve **Update app**
   for missing settings; setup never changes an existing app silently.
   Slack CLI hands the Socket Mode and bot tokens over privately; hidden
   prompts are a fallback. Setup checks workspace and app identity.
5. Select joined or public channels, or none: new Tags follow invitations.
   Setup validates the history credential before writing a connector limited
   to those channels; it never indexes during setup.
6. Run `tag start`.
7. Mention the bot in each selected test channel and record an observed reply;
   service readiness alone is not an end-to-end pass.
8. Send Tag (or the customized app name) a direct message and confirm an
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
   the computer account's first name plus **Tag** and uses a Tag waterdrop no
   other Tag on the computer uses; setup can change both, and add a one-line
   description, before creation.
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
   - `reactions:read` + `reactions:write` (optional) — see 👎 feedback and react, for channel watching. Without them, watching stays off.
   - `team:read` (optional) — read the workspace icon that Tag.app shows beside the workspace name. Without it, Tag still starts and Tag.app shows just the workspace name.
   - `users:read` — verify app identity and caller visibility for cross-channel search.
5. Open **Event Subscriptions** and subscribe to Bot Events:
   - `app_mention`
   - `message.channels`
   - `message.groups`
   - `message.im`
   - `reaction_added`
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

## Channel watching (experimental)

Channel watching lets Tag answer in a channel without an @mention. It is off
by default and set per channel. [ADR 0010](../docs/adr/0010-channel-watching.md)
records why it works this way.

**What Tag watches.** In a watched channel, Tag looks only at messages from
authorized people that:

- name it without an @mention, such as “Iris's Tag, what do you think?”; or
- continue a thread where someone @mentioned Tag or engaged with it.

Watching every message in a channel is not available.

**Watched replies have reduced capability.** A message that nobody @mentioned
is untrusted input: anyone in the channel can write text that games the
classifier. A watched reply comes from a text-only run of the Tag's own model:

- no tools, shell, files, workspace, memory, search or credentials;
- its only input is the thread, with member IDs replaced by roles.

Tag answers with nothing, an 👀 reaction, or one short line in the same thread,
never at the top level. Each reply says why Tag replied and has two buttons:

- **Take this on** starts a normal run for the authorized person who clicks it.
- **Don't do this here** turns watching off in that channel. Anyone can click
  it.

Any action, change, file or search needs that confirmation, or an @mention.

**Stages.** Each stage runs only if the previous one passes:

1. Free checks. Watching stays quiet for:
   - bots, edits and Tag's own messages;
   - unwatched channels and unauthorized people;
   - member mentions (they keep their usual paths);
   - quiet hours;
   - a thread that already has an unprompted reply;
   - the daily limit, a cooldown, or a Slack rate limit;
   - a message that neither names Tag nor continues a thread it's in.
2. The classifier scores the thread context.
3. The text-only draft may still decide to stay quiet.

**Limits.**

- One unprompted reply per thread until someone engages explicitly: an
  @mention, **Take this on**, or 👍 on the reply.
- `OPENTAG_WATCH_DAILY_LIMIT` unprompted replies per channel per day (default
  `5`).
- `OPENTAG_WATCH_COOLDOWN_HOURS` of quiet in a channel after a 👎 or a “stop”
  reply (default `24`).
- `OPENTAG_WATCH_QUIET_HOURS` in this computer's time (default `19:00-08:00`;
  empty for none).
- Tag also pauses watching when Slack answers 429.

**Turning it on.** Each channel has a mode: `off` (the default),
`when-addressed` or `chimes-in`. An authorized person picks the channels for
each mode on Tag's Home tab, or sets them with `OPENTAG_WATCH_CHANNEL_MODES`.
`tag start` moves channels from the older `OPENTAG_WATCH_CHANNEL_IDS` list to
`when-addressed`, once. Tag first posts a notice in
each channel explaining what it watches and how to turn it off, and watches only
after that notice is posted. Watching also needs the optional `reactions:read`
and `reactions:write` permissions, which `tag start` requests; until Slack
grants them, watching stays off. Add other names people use for the Tag with
`OPENTAG_WATCH_NAMES`.

**Classifier.** Each Tag picks one classifier, and each has its own private key,
model and threshold:

| Provider | `OPENTAG_WATCH_CLASSIFIER` | Key | Model | Threshold |
| --- | --- | --- | --- | --- |
| OpenAI Decisions (beta, default) | `openai-decisions` | `OPENTAG_OPENAI_DECISIONS_API_KEY` | `OPENTAG_DECISIONS_MODEL`, default `gpt-6-luna` | `OPENTAG_DECISIONS_THRESHOLD`, default `0.95` |
| TypeSafe Jev | `typesafe-jev` | `OPENTAG_TYPESAFE_API_KEY` | `OPENTAG_JEV_MODEL`, default `jev-latest` | `OPENTAG_JEV_THRESHOLD`, default `0.9` |

Decisions became the default on 26 Oct 2026: on real Slack messages it was
right 99% of the time at 0.95 (see `evals/channel_watch/`). A Tag that already
watched channels without choosing a provider keeps TypeSafe Jev; `tag start`
saves that choice once.

```sh
printf '%s' "$OPENAI_DECISIONS_KEY" | tag config set OPENTAG_OPENAI_DECISIONS_API_KEY --stdin --json
tag config set OPENTAG_WATCH_CHANNEL_MODES C0123456789=when-addressed --json
tag restart
```

Both providers get the same question: is the person talking to Tag, directly
or as part of the audience, such that a reply is welcome now?

**Chime-in.** In `chimes-in` channels, every new top-level post from a person
also gets a second question: does it invite colleagues' reactions, such as a
launch, demo, release or win? Requests, incidents, routine status and
sensitive personal news are no. It has its own threshold per provider,
`OPENTAG_JEV_CHIME_THRESHOLD` or `OPENTAG_DECISIONS_CHIME_THRESHOLD` (default
`0.95`), and both questions go in one call when a message could also be
addressed to Tag. A chime-in is a short comment in the thread, a 🎉 reaction or
nothing; it offers no actions. Every post is a paid classifier call, so cost
grows with the channel's traffic.

**Attachments, read-only.** The classifier sees only a text description of a
watched message's uploads and link previews: names, types, video length and
preview titles, never URLs or contents. When it says yes, the draft run, which
still has no tools, also gets up to 3 images (5 MB each), a video's one preview
frame, the video's Slack captions as text, and link previews as text. The
bridge downloads them with the bot token, only from Slack's file hosts, into a
private temporary folder that is deleted after the run. There is no `ffmpeg`
step and no transcription service.

**Digests of shared links and files (experimental).** In channels listed in
`OPENTAG_DIGEST_CHANNEL_IDS`, off by default, Tag digests posts from authorized
people that are mostly a link or files: an X post, a web page, a Slack upload.
A free rule decides; questions, requests to a Tag and links to your own sites
(`OPENTAG_DIGEST_OWN_HOSTS`) are skipped. The bridge reads public pages with a
plain request and X posts through X's free oEmbed endpoint, without cookies or
logins, within size caps, and never from private network addresses. Drive and
Baidu links are noted but not read yet, and access codes are never kept. One
text-only run on the Tag's own backend writes the summary, which goes in a
private store (`state/digests.jsonl`, kept 90 days) marked as an unverified
digest. Tag reacts 📑 when it digests a post and 🔒 when it couldn't read
anything; it doesn't reply. Search the store with `tag config digests [QUERY]
--json`. At most `OPENTAG_DIGEST_DAILY_LIMIT` (default `20`) digests per channel
a day.

**Cost per channel.** Tag counts every classifier call per channel, with its
input tokens and a spend estimate at the provider's list price. A failed call
counts at its estimated size. See them with `tag config watching` (add `--json`
for scripts) or on the Home tab, under the channel pickers. The provider's
dashboard has the billed amount.

- Jev answers it as a `noul` question with true and false criteria about a
  structured state.
- Decisions answers it as a `predicate`, with the same criteria plus six
  fixed synthetic examples in the instructions, and the state serialized into
  `input`.

A timeout (`OPENTAG_WATCH_CLASSIFIER_TIMEOUT_SECONDS`, default `5`), error,
refusal or malformed answer stays silent.

**Without a key.** If the selected classifier's key isn't set, watching and
chime-in are off and the rest of the Tag works as usual. `tag config show`,
`tag config watching`, the Home tab and the startup summary say why, and Tag
posts no notice until the key is set. Using the Tag's own backend as the
classifier (one tiny yes-or-no text-only call) is built but not offered yet: it
needs its own evaluation first.

For each message that passes the free checks, the classifier receives:

- the message text;
- the Tag's name and role;
- up to ten recent text messages from the same thread.

Member IDs become roles (`tag`, `requester`, `participant`, `another_bot`).
Attachments, files and the rest of the channel are excluded. The keys and
settings stay in the bridge; the local agent never receives them.

**Tuning.** Tag logs every score, its outcome and the minimized thread state to
`state/channel-watch-scores.jsonl` in its private folder. 👎, “stop” and
**Don't do this here** label a reply negative; **Take this on** and 👍 label it
positive. The thresholds above are unmeasured defaults. Export the log with
`scripts/watch_eval.py export`, label the rest by hand, and change a threshold
only after 200 or more labelled messages, aiming for precision (see
`evals/channel_watch/README.md`).

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

`SLACK_ALLOWED_USER_IDS` is required and fails closed when empty. Setup writes
the member ID of the person signed in to Slack as the owner-only default; append
comma-separated member IDs (in Slack: your profile → **More** → **Copy member
ID**) only when the owner intentionally shares access. Unauthorized mentions receive a denial without
reading the thread or invoking the backend. Existing installations must add this
setting before restarting Tag.

Direct-message invocation is enabled by default for the same authorized users,
who can invoke Tag without an `@mention` from Tag's Messages tab. The channel
allowlist does not apply to DMs, but `SLACK_ALLOWED_USER_IDS` still does. Set
`OPENTAG_SLACK_DM_ENABLED=0` to disable DM invocation. Set
`OPENTAG_SHOW_THINKING=0` to keep thinking summaries out of Agent
activity and the Activity record. Each top-level DM starts a
fresh backend task, while replies reuse only that DM thread's bounded context
(up to 30 messages).

`OPENTAG_PEER_TAGS` lists other Tags this Tag may ask for help and accept
requests from, as comma-separated `Name=MEMBERID` pairs that use each Tag's bot
member ID, for example `Research Tag=U0123ABCD,Writer Tag=U0456EFGH`. It is
empty by default, which turns handoffs off. Mentions from bots that are not
listed are ignored. See `references/handoffs.md`.

`OPENTAG_TEST_APP_IDS` is for automated live tests of Tag itself, not for
normal use. It lists Slack app IDs whose user-token posts Tag answers as the
person who posted them (Slack marks those posts with the app's bot id). The
person must still be in `SLACK_ALLOWED_USER_IDS`. It is empty by default and
`tag config` does not show it; set it by editing the Tag's `settings.json`.

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
export OPENTAG_THREAD_MAX_CONTEXT_TOKENS=150000 # start a fresh thread conversation at this size (0: never continue)
export OPENTAG_THREAD_IDLE_HOURS=4  # start a fresh thread conversation after this idle time (0: never continue)
export OPENTAG_SLACK_STREAMING=0    # optional: disable default Slack response streaming
export OPENTAG_CODEX_TRANSPORT=exec # optional legacy rollback; App Server is the default
export OPENTAG_CODEX_MODELS=""      # optional comma-separated model allowlist
export OPENTAG_CODEX_REASONING_EFFORTS="low,medium,high,xhigh,max,ultra"
```

The native Slack loading indicator and response streaming are enabled by
default. Claude provides live answer deltas. Codex defaults to App Server for
phased final-answer deltas, observed activity, and interruption; set
`OPENTAG_CODEX_TRANSPORT=exec` only for legacy rollback. Tag does not
forward commentary, reasoning, tool output, or raw backend diagnostics into
the shared Slack thread. Bounded tool previews appear only in the requester's
Activity modal.

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
and `MCP_TOOL_NAMES` in `scripts/agent_activity.py` to approve more display
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

All requests to a Tag use its configured model and thinking level, selected
in Tag.app → Details or `tag NAME settings ai`. This applies to Codex and
Claude. Slack replies have no Configure control or per-user preferences.
Startup archives legacy preferences before accepting requests; historic
Configure buttons explain where settings moved and cannot save overrides.
Slack copy says "the Tag app" rather than "Tag.app", because Slack turns
`Tag.app` into a link to the tag.app website. The bridge links that phrase to
`hover-tag://tag/TAG_ID`, which Tag.app registers to open that Tag's Details.
Backend-supported Fast Mode defaults still apply. Restart Tag after changing
its local backend configuration; app model changes restart it automatically.

When the diagnostic Activity control is enabled, it opens saved activity.
Failed and stopped runs show **Activity** alongside their existing actions. The button
opens a Slack modal only for the original requester, after checking the current
user allowlist and conversation policy. The first view is a compact timeline of
tool labels, timestamps, and status. Selecting **Details** for a step opens a
second modal view with its available input and result; **Back** returns to the
timeline. Tag checks the original requester again for every detail action.
Common credential patterns and secret-named fields are redacted, and detail
previews are shortened before they leave the backend process. Tag checks them
again before storage. It stores this bounded record under
the instance's private `state/activity/` directory. Records are available for
30 days; later requests prune expired records and keep at most 200 runs, with
up to 60 events per run. It does not store or display raw App Server items,
prompts, or raw reasoning. Thinking steps store the model's bounded thinking
summary as their result unless `OPENTAG_SHOW_THINKING=0`. An item marked complete
means the tool call ended; it does not verify that an external action succeeded.
App Server can report an outer agent-tool step without exposing connector calls
made inside that step, so this view is an activity summary rather than a
complete audit log. The legacy Codex exec and Claude transports do not provide
this record.
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
