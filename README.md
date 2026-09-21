<!-- Modified by klovr.co in 2026 for Tag. See NOTICE and repository history. -->

# Tag

## @Tag, Slack is all you need.

Tag brings Codex or Claude into the Slack conversation. Mention your Tag where the
work is already being discussed. It picks up the thread, finds any context it is
allowed to use, does the work, and replies there.

Your team stays in Slack. No one has to copy a conversation into a private AI
chat and carry the answer back.

Tag is an open-source reference implementation inspired by
[Claude Tag](https://www.anthropic.com/news/introducing-claude-tag). It connects
Slack to a local CLI agent and uses
[MFS](https://github.com/zilliztech/mfs) as searchable memory.

> [!NOTE]
> The `main` branch is intentionally Slack-only for the v0.1 alpha launch.
> Unfinished Zulip work is preserved on [`feature/zulip`](https://github.com/klovr-co/tag/tree/feature/zulip),
> outside the supported installer and runtime.

## Why I built this

I first saw Claude Tag being shared on X and wanted the same experience: mention
Claude in Slack, give it the context of the team's conversation, and let everyone
see the work happen.

The hosted launch was aimed at Claude Team and Enterprise workspaces. I was not
subscribed to one of those plans. I already had my own Claude access and wanted
to use it with my own Slack workspace.

I found the original Open Tag example in MFS, forked it, and spent about a month
adapting it to the way I work. It became useful for more than answering a single
question. Tag can read approved Slack history, follow a discussion across a
thread, retrieve related context, and share the result back where the team is
already working.

That is the part I care about: the discussion and the result stay visible in
Slack. They do not disappear into my private Claude or ChatGPT history.

## What your team can delegate

- Respond when someone mentions their Tag in Slack or sends it a direct message,
  provided the sender is explicitly authorized.
- Read the current thread, including text and image attachments.
- Upload backend-generated PNG, JPEG, GIF, and WebP images to the requesting thread.
- Summarize an indexed Slack channel instead of seeing only one thread.
- Search approved Slack history, repositories, documents, issues, databases,
  and object stores through MFS.
- Run real tasks through Claude Code or Codex in a configured workspace.
- Keep long answers readable by splitting them into threaded Slack replies.
- Post a requested summary back into the current channel.

## See it in action

The [connected user-flow guide](docs/user-flows.md) shows what happens during
setup, a Slack request, MFS retrieval, workspace work, and error recovery. To
open the designed light-mode version, serve the repository locally:

```bash
python3 -m http.server 8765
```

Then visit <http://127.0.0.1:8765/docs/user-flows.html>.

### Delegate work across channels

A teammate requests a PR review in one channel. From another channel, someone
mentions the bot and asks it to handle the review. Tag finds the original
request in indexed Slack history, retrieves the PR context, and reports back in
the thread.

![Tag reviewing a PR using context from another Slack channel](https://github.com/user-attachments/assets/6cb1db05-dd12-4a13-a9fa-1a1bf69bcf28)

### Continue the discussion with shared context

A follow-up asks the bot to compare two projects and write up the differences.
Tag keeps the thread context, gathers information from the approved sources, and
returns the result where the rest of the team can read and continue the work.

![Tag completing a follow-up task across multiple sources](https://github.com/user-attachments/assets/8f11e931-4248-46c5-b1fb-8128d56b8773)

## How it works

```text
       ┌──────────────┐
       │    Slack     │    @Maya's Tag <task>
       │              │ ◄──── answer ──────┐
       └──────┬───────┘                    │
              │ mention                    │
              ▼                            │
   ┌────────────────────────────────────┐  │
   │                Tag                 ├──┘
   │   Brain: Claude Code or Codex CLI  │
   └────────────────┬───────────────────┘
                    │ scoped retrieval
                    ▼
   ┌────────────────────────────────────┐
   │                MFS                 │
   │ Slack · repos · docs · issues · DB │
   └────────────────────────────────────┘
```

Tag has three parts:

- **Brain:** Claude Code or Codex runs the task locally.
- **Memory:** MFS indexes the sources you approve and makes them searchable.
- **Chat:** Slack supplies the conversation and receives the answer.

Tag does not call a model API directly. Authentication, model access, and usage
come from the CLI backend installed on your machine.

## Quick start

TAG provides installers for macOS, Linux, and native Windows. The primary path
is Slack + Codex + local MFS; Claude Code remains experimental. Native Windows
live Slack/backend qualification is still required before release.

You need Python 3.10+, `curl`, and a working Codex CLI login. The installer uses
[`uv`](https://docs.astral.sh/uv/) when available and otherwise falls back to
Python's standard `venv` and pip. Clone Tag first:

```bash
git clone https://github.com/klovr-co/tag.git
cd tag
```

### Agent-guided setup (recommended)

Install Tag's setup skill for Codex:

```bash
npx skills add klovr-co/tag --skill hover-tag-setup -a codex -g
```

Open a new Codex task in the cloned repository and ask: `Set up Tag for me.`
The agent can check prerequisites, run the installer, and diagnose failures. It
will pause when Slack requires you to create or approve the app.
Once installed, the skill starts with `tag inspect --json` and uses targeted
configuration commands, asking only for missing information.

### Manual setup

Run the same guided installer yourself:

```bash
./install.sh
```

The installer creates a permanent application home and an isolated runtime,
independent of this checkout. Add its printed command directory to PATH, then
run `tag` for status and next steps, or `tag setup` for resumable setup. Use `tag reset`
to back up the old setup and redo onboarding after confirmation. Windows users
run `./install.ps1` from PowerShell instead. See [installation and TAG home](docs/installation.md)
for platform paths, download installers, skills, MCP, and migration.
Contributors running directly from a checkout must first run
`./install.sh --dependencies-only`; `./tag` deliberately does not fall back to
system Python or install dependencies during startup.
`tag setup` owns the Slack journey. It reuses the installed Slack CLI, offers
the CLI's real login flow when the sandbox workspace is not authorized, and
then lets you create a manifest-based app or link an existing app by App ID.
Profile-picture selection and upload require Slack CLI 4.7 or newer.
For a new app, setup proposes **&lt;your first name&gt;'s Tag** and a uniquely
curated Tag waterdrop, selected from 144 approved base designs and 16 subtle
signatures. You can edit the name or choose your own picture by
dragging a local PNG, JPEG, or GIF into the terminal. The picture is copied into
Tag's private application home and passed to Slack CLI during app creation.
It pauses for every Slack approval that only a person or workspace admin can
grant. Tokens are entered only through hidden terminal prompts.

The menu shows the next useful action based on current settings and service
health. Settings and Troubleshooting remain available when you return. For
scripts and skills, use the same operations directly:

```bash
tag inspect --json
tag config init --json
tag config show --json
tag config set OPENTAG_BACKEND codex --json
tag doctor --json
```

Settings output redacts secrets. Existing settings survive initialization and
setup retries. See [setup and management](docs/tag-management.md) for the command
contract, secret input, experimental Claude selection, and recovery.

After the bot token is validated, setup shows the Slack channels visible to the
bot and lets you select one or more joined channels by name. It separately
validates the Socket Mode, bot, and Slack-history credentials, then asks before
writing a selected-channel-only MFS connector. For another channel, invite the
bot there first and rerun setup. Once Tag is running, authorized owners can also
change reply destinations with the searchable picker in Slack App Home; rerun
setup before expecting a newly added destination to have indexed memory.

Start Tag and inspect it with:

```bash
tag start
tag status
tag logs
tag logs --follow
```

To connect another independent Slack workspace/app through the same installed
runtime, create and target a named Tag:

```sh
tag add
tag list
tag personal start
tag personal status
```

Unqualified commands operate on the default Tag at `instances/default`. Every
Tag keeps separate settings, Slack identities, conversations, and bridge
lifecycles there, with its user-editable workspace at `~/Tag/NAME`; they reuse
one installation-owned MFS service without gaining cross-workspace retrieval. See
[Tag management](docs/tag-management.md#multiple-slack-workspaces).

Prefer the dedicated `tag restart` command over chaining stop and start so the
terminal presents one coherent operation. Use `tag doctor` for deeper
diagnostics after the quick status and recent logs.

When developing from a prepared source checkout, use `./tag dev`. It watches
`scripts/**/*.py`, reloads only the Slack bridge after changes, and streams its
output in the foreground. For the default loopback endpoint, Tag owns MFS as
well: an identifiable untracked server is replaced with the checkout's runtime,
and Ctrl-C stops both development services. Explicit remote MFS endpoints remain
externally managed. This command is intentionally unavailable from managed
releases.

Mention your Tag in the test channel you configured. This example uses Maya's
Tag; select your own bot's mention:

> @Maya's Tag read the project documentation in your workspace, summarize what this
> project is trying to accomplish, and cite the supporting files.

This first request uses the local workspace configured during setup. A request
to summarize the entire Slack channel requires that channel's history to be
indexed separately through an MFS Slack connector.

Only the owner member ID entered during setup can invoke Tag initially. Add
other IDs to the comma-separated `SLACK_ALLOWED_USER_IDS` setting to share access.
Authorized users can also invoke Tag without an `@mention` from the app's
Messages tab. Each top-level DM starts a fresh task; replies in that DM thread
provide bounded context only for that task. Set `OPENTAG_SLACK_DM_ENABLED=0` to
disable direct-message invocation.

While a task runs, Tag uses Slack's native loading indicator instead of posting
a temporary bot message. Slack response streaming is enabled by default.
Claude streams answer deltas directly. Codex uses App Server by default to stream
final-answer deltas, display activity backed by observed tool events, and honor
Slack's native Stop button. Set `OPENTAG_CODEX_TRANSPORT=exec` for rollback, or set
`OPENTAG_SLACK_STREAMING=0` to retain buffered replies for troubleshooting.
Commentary, reasoning, tool output, and raw diagnostics are never streamed.
Capacity and rate-limit failures are retried before observable work begins; the
loading indicator shows the attempt count. A terminal failure clears the loading
state, posts sanitized guidance with a local-log reference, and offers a **Retry**
button that reloads the original Slack request.
Tag also journals active Slack thread identities in its private state directory.
Normal shutdown clears those sessions before exit; after a forced crash, the
next start clears any stale Slack working indicators before accepting new work.

Codex replies also include a compact **Configure** button beneath the answer. It
opens a modal that saves model, native Codex reasoning-level,
and Fast Mode choices for that Slack user across channels and threads.
The modal's **Reset to default** button restores every control before saving.
Its defaults come from `~/Tag/NAME/.codex/config.toml`, layered over the user's
global `~/.codex/config.toml`; restart Tag after editing the local file.
Fast Mode is independent of
reasoning level and uses increased usage for faster responses. Operators can
restrict the selectable models with `OPENTAG_CODEX_MODELS` and the reasoning
levels with `OPENTAG_CODEX_REASONING_EFFORTS`.

Stop one bridge with `tag stop` (or `tag NAME stop`). Shared memory stays
online for other Tags; inspect or explicitly stop an installation-owned service
with `tag memory status` / `tag memory stop`. Independently started MFS servers
are left running.

The admin skill also supports later configuration and troubleshooting. It
cannot create or approve a Slack app on behalf of your workspace administrator.

### Upgrade or uninstall

Install Tag once, then use `tag upgrade`; it follows the channel selected during
installation, verifies the release, preserves configuration and personal data,
and restarts running Tag services on the new release. Use
`tag upgrade --dry-run` to check first, `tag upgrade --channel stable` to switch
channels, or `tag upgrade --version X.Y.Z` to install and pin an exact release.
Tag blocks older versions unless `--allow-downgrade` is explicitly supplied;
prefer `tag rollback` for the immediately previous release. Use
`tag migrate --from /path/to/old/checkout` to copy legacy configuration and
skills without deleting the originals.

Tag checks the saved release channel at most daily during normal human-readable
status, inspection, setup, and start flows. A newer published release produces
an advisory `tag upgrade` prompt; offline checks are silently skipped and never
block the command. Source installs without a saved channel are compared with
the default alpha channel and, when behind, prompted to run
`tag upgrade --channel alpha`. Machine-readable JSON remains clean.

To uninstall, stop TAG, back up personal files, then remove its managed launcher
and application home. See [installation](docs/installation.md) for details.
For source development, use `./tag` with an isolated absolute `TAG_HOME`.

## Slack credentials

Tag uses Slack credentials in two separate places:

| Credential | Purpose |
|---|---|
| `SLACK_APP_TOKEN` (`xapp-…`) | Opens the Socket Mode connection that receives mentions. |
| `SLACK_BOT_TOKEN` (`xoxb-…`) | Reads permitted conversations and posts replies. |
| MFS Slack connector token | Indexes only the channels explicitly approved during setup as durable memory. |

The local agent backend inherits `SLACK_BOT_TOKEN` and `MFS_TOKEN`. Tag withholds
the Socket Mode token, Slack-history connector token, and bridge access-control
configuration from that child process. Its Slack and MFS helper restrictions
are application guardrails—not a hardened capability boundary: the backend
still runs as the same local account and can access whatever that account can.
Run Tag with dedicated, least-privilege credentials in an isolated environment.

The bridge app normally needs these bot scopes:

- `app_mentions:read`
- `users:read` (verify app identity and cross-channel caller visibility)
- `assistant:write`
- `chat:write`
- `files:read` and `files:write`
- `channels:read` and `channels:history`
- `groups:read` and `groups:history` if you intentionally use private channels
- `im:history` for requests from the app's Messages tab

It also needs the `app_mention`, `message.im`, `app_home_opened`, and
`agent_session_stopped` bot events and an app-level token with
`connections:write`. Invite the bot only to channels where it should respond.
`files:read` supports input attachments, while `files:write` supports explicitly
requested generated-file delivery through private Slack file links, including
backend-generated images. Each requested output also gets its own **Open
filename** button. The button is restricted to the requesting Slack user and
opens that workspace file with the default desktop application on the machine
running Tag; the private Slack link remains available on other devices.
Direct-message execution is enabled by default and can be disabled with
`OPENTAG_SLACK_DM_ENABLED=0`.
The included app manifest also requests `canvases:write` for the explicit Canvas
helper. Reinstall the Slack app after adding any scope.

On upgrade, `tag start` compares the linked app with Tag's versioned manifest
requirements and applies pending additive migrations before services start.
Existing app-specific settings are preserved. If a migration adds an OAuth
scope, Slack still requires the owner or workspace admin to approve that new
permission; Tag opens the reinstall flow and refreshes its saved credentials
instead of requiring manual manifest editing.

By default Slack lets workspace members install apps, but a workspace owner or
Enterprise organization can require approval. In that case, request approval
from a workspace owner or app manager before continuing setup.

For the complete setup, token model, and troubleshooting checklist, read
[the Slack adapter guide](references/slack-adapter.md).

## Give Tag memory

Tag can only retrieve sources that meet both conditions:

1. the source has already been indexed by MFS; and
2. its root is listed in `MFS_ALLOWED_SCOPES`.

For example:

```bash
export MFS_ALLOWED_SCOPES="slack://team-memory,file://local/path/to/repo"
```

MFS supports Slack, local files, GitHub, Jira, Linear, Postgres, MongoDB,
BigQuery, S3, and other connectors. Connector credentials remain under your
control. Tag consumes indexed sources; it does not silently add new ones.

The scope helper rejects reads and directory listings outside the configured
roots. The underlying connector credentials and source allowlists remain an
additional boundary.

### Permission-aware Slack history search

Slack history stays isolated to the channel that invoked Tag by default. A
normal question, including one about a broad topic, receives exactly that
channel's indexed scope. An authorized caller can expand the search explicitly:

- `search #support and #engineering for the rollout decision`
- `look across Slack for earlier reports of this error`
- `check all channels I can access for the customer name`

The runtime agent understands the request and decides whether to use the normal
current-channel MFS helper or `scripts/slack_history_search.py`. That dedicated
helper searches all permitted indexed channels by default, or named channels
selected with repeated `--channel` arguments. The model decides when to call the
tool, but it cannot add channels to the tool's bridge-generated grant.

Before starting Codex or Claude, Tag resolves the permitted grant to stable
Slack channel IDs. The eligible set is the intersection of the installation's workspace,
operator-approved channels, channel-specific MFS scopes, and channels whose
visibility Tag can currently prove for the caller. Private channels and channels
used by restricted or guest users require live membership proof. Archived,
Slack Connect/shared, stale, unindexed, inaccessible, or API-unverifiable
channels are omitted. A named channel that is absent or non-unique in that grant
is rejected by the helper without searching. The agent can then ask the user to
clarify without receiving data from an unverified channel.

Search results identify their source channel. Channel names are display
metadata; authorization continues to use the stable ID, so a rename does not
change the grant. Each installation accepts scopes only from its configured
Slack workspace authority, preventing scopes from another deployed app or
workspace from joining the search.

This feature strengthens Tag's normal helper guardrails but does not change the
[credential boundary](docs/adr/0001-credential-boundary.md): the local backend
still inherits credentials in a trusted sandbox. The first implementation uses
a backend-neutral Python policy module and CLI adapter; it does not add MCP.

See [Memory](references/memory.md) for the retrieval model and the
[MFS connector documentation](https://github.com/zilliztech/mfs/tree/main/docs/connectors/)
for available sources.

## Security model

Tag turns chat messages into instructions for a local coding agent. Treat every
message and attachment as untrusted input.

Current safeguards include:

- MFS scope checks for search, read, and directory listing;
- a required Slack caller allowlist seeded with the owner during setup;
- an optional `SLACK_CHANNEL_ID` gate;
- withholding of the Socket Mode token and bridge access-control settings from
  backend processes;
- bounded attachment size and thread context;
- task timeouts and limited retries;
- automatic Codex workspace safety review.

The default task watchdog stops a backend after seven minutes without a
recognized lifecycle event, while a separate one-hour maximum still bounds an
active task. Slack's processing-status refresh does not extend either deadline.

The backend's inherited credentials can be used directly by tools or shell
commands, bypassing Tag's scoped helpers. Tag does **not** provide a hardened
sandbox, organization-wide identity policy,
auditable approvals, spend controls, or enterprise administration. Claude Code
currently runs with permission checks skipped. Locally installed tools use their
own credentials and permissions.

Use a non-production host or a real external sandbox for stronger isolation.

## Documentation

- [Documentation home](docs/index.md)
- [Why Tag](docs/philosophy/why-tag.md)
- [How Tag works](docs/concepts/mental-model.md)
- [Run your first task](docs/getting-started/first-task.md)
- [Supported capabilities](docs/reference/supported-capabilities.md)
- [Connected user flows and functional tour](docs/user-flows.md) ([light-mode HTML](docs/user-flows.html))
- [Slack setup and troubleshooting](references/slack-adapter.md)
- [Backend behavior](references/backends.md)
- [Runtime agent contract](references/runtime-agent.md)
- [Memory model](references/memory.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Security policy](SECURITY.md)
- [Release contract](RELEASE.md)
- [Changelog](CHANGELOG.md)

Maintainers can run the same validation used by GitHub Actions with:

```bash
./scripts/ci_check.sh
```

The separate install-smoke workflow runs `./install.sh --dependencies-only` and
`./tag doctor --offline` from clean macOS and Linux runners without credentials.

## Origins and attribution

Tag began as a modified derivative of the
[Open Tag Example](https://github.com/zilliztech/mfs/tree/main/examples/open-tag-skill)
from [Zilliz MFS](https://github.com/zilliztech/mfs). The upstream material is
licensed under the Apache License 2.0. This repository contains subsequent
modifications and extensions.

## Status

Tag is an early open-source project built from a workflow that has already been
useful in day-to-day Slack discussions. It is ready for experimentation in a
trusted sandbox. It is not a production security boundary.

Issues and contributions are welcome.
