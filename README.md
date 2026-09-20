<!-- Modified by klovr.co in 2026 for Tag. See NOTICE and repository history. -->

# tag

Bring Codex into Slack as a shared, self-hosted teammate.

Mention the bot in a channel, let it read the conversation and your approved
sources, and delegate real work without moving the discussion into one person's
private AI chat.

Tag is an open-source reference implementation inspired by
[Claude Tag](https://www.anthropic.com/news/introducing-claude-tag). It connects
Slack to a local CLI agent and uses
[MFS](https://github.com/zilliztech/mfs) as searchable memory.

> [!NOTE]
> The `main` branch is intentionally Slack-only for the v0.1 alpha launch.
> Unfinished Zulip work is preserved on [`feature/zulip`](https://github.com/klovr-co/tag/tree/feature/zulip),
> outside the supported installer and runtime.

> [!WARNING]
> Tag is an alpha and is not a production security boundary. Start in an
> isolated channel, point it at a sandbox workspace, and invite only people you
> trust. The agent can read and change files using the permissions of the local
> account that runs it.

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

## What Tag can do

- Respond when someone mentions `@OpenMax` in Slack.
- Read the current thread, including text and image attachments.
- Summarize an indexed Slack channel instead of seeing only one thread.
- Search approved Slack history, repositories, documents, issues, databases,
  and object stores through MFS.
- Run real tasks through Claude Code or Codex in a configured workspace.
- Keep long answers readable by splitting them into threaded Slack replies.
- Post a requested summary back into the current channel.

## See it in action

The [connected user-flow guide](docs/user-flows.md) follows the complete journey
from setup and caller authorization through thread context, MFS retrieval,
workspace work, shared outputs, and recovery. For the designed light-mode view,
serve the repository locally and open the rendered tour:

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
       │    Slack     │    @OpenMax <task>
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

You need Python 3.10+, [`uv`](https://docs.astral.sh/uv/), `curl`, and a working
Codex CLI login. Clone Tag first:

```bash
git clone https://github.com/klovr-co/tag.git
cd tag
```

### Agent-guided setup (recommended)

Install Tag's admin skill for Codex:

```bash
npx skills add klovr-co/tag --skill open-tag-admin -a codex -g
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

Prefer the dedicated `tag restart` command over chaining stop and start so the
terminal presents one coherent operation. Use `tag doctor` for deeper
diagnostics after the quick status and recent logs.

When developing from a prepared source checkout, use `./tag dev`. It watches
`scripts/**/*.py`, reloads only the Slack bridge after changes, and streams its
output in the foreground. Press Ctrl-C to stop the development bridge; MFS is
left running. This command is intentionally unavailable from managed releases.

Mention `@OpenMax` in the sandbox channel you configured:

> @OpenMax summarize this channel and list the decisions and open questions.

Only the owner member ID entered during setup can invoke Tag initially. Add
other IDs to the comma-separated `SLACK_ALLOWED_USER_IDS` setting to share access.

While a task runs, Tag uses Slack's native loading indicator instead of posting
a temporary bot message. Slack response streaming is enabled by default:
Claude responses stream into the thread as answer deltas arrive, while Codex
shows the native loading state and then posts its completed answer because the
Codex CLI currently emits final-message events. Set
`OPENTAG_SLACK_STREAMING=0` to retain buffered replies for troubleshooting.

Codex replies also include a compact **Configure** button beneath the answer. It
opens a modal that saves model, native Codex reasoning-level,
and Fast Mode choices for that Slack user across channels and threads.
The modal's **Reset to default** button restores every control before saving.
Fast Mode is independent of
reasoning level and uses increased usage for faster responses. Operators can
restrict the selectable models with `OPENTAG_CODEX_MODELS` and the reasoning
levels with `OPENTAG_CODEX_REASONING_EFFORTS`.

Stop TAG-managed processes with `tag stop`. Independently started MFS servers
are left running.

The admin skill also supports later configuration and troubleshooting. It
cannot create or approve a Slack app on behalf of your workspace administrator.

### Upgrade or uninstall

To upgrade, rerun the installer, then `tag stop` and `tag start`. Configuration,
personal skills, MCP settings, and state are preserved. `tag rollback` selects
the previous release while stopped. Use `tag migrate --from /path/to/old/checkout`
to copy legacy configuration and skills without deleting the originals.

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
- `chat:write`
- `channels:read` and `channels:history`
- `groups:read` and `groups:history` if you intentionally use private channels

It also needs the `app_mention` and `app_home_opened` bot events and an app-level token with
`connections:write`. Invite the bot only to channels where it should respond.
The included app manifest also requests `files:read` for text attachments and
`canvases:write` for the explicit Canvas helper.

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

The backend's inherited credentials can be used directly by tools or shell
commands, bypassing Tag's scoped helpers. Tag does **not** provide a hardened
sandbox, organization-wide identity policy,
auditable approvals, spend controls, or enterprise administration. Claude Code
currently runs with permission checks skipped. Locally installed tools use their
own credentials and permissions.

Use a non-production host or a real external sandbox for stronger isolation.

## Documentation

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
trusted sandbox—not as a production security boundary.

Issues and contributions are welcome.
