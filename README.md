# Open Tag Example

[Claude Tag](https://www.anthropic.com/news/introducing-claude-tag) is Anthropic's
way to bring Claude into Slack as a shared teammate: an admin grants it access to
selected channels, tools, and data, and anyone in the channel can `@Claude` to
delegate a task while they get on with other work. It is a hosted product for
Claude Enterprise and Team.

Open Tag is a small open-source homage to that pattern, built on top of MFS: you
mention a bot in Slack, it gathers authorized context, and a CLI agent backend
does the work. By convention it answers to **`@OpenClaude`** (Claude backend) or
**`@OpenCodex`** (Codex backend), so it reads like the official `@Claude` tag. The
difference is that the Memory and Tools are your own self-hosted MFS sources,
behind whatever CLI agent you run — your data and credentials stay on your machines.

The example maps the core concepts onto MFS:

- **Memory**: authorized Slack, repo, docs, issue, database, or object-store
  context indexed by MFS.
- **Tools**: external sources exposed through MFS connectors plus the workspace
  tools granted to the backend.
- **Brain**: a CLI agent backend — `claude -p` (Claude Code) or `codex exec`
  (Codex).

## Architecture

Open Tag is a thin layer of glue; all the retrieval power lives in MFS.

```text
       ┌──────────────┐
       │    Slack     │   @OpenClaude  <ask>
       │   (thread)   │ ◄──── answer ────┐
       └──────┬───────┘                  │
              │ mention                  │
              ▼                          │
   ┌──────────────────────────────────┐ │
   │         Open Tag  (glue)         ├─┘
   │   🧠 Brain = CLI agent backend   │
   │      claude -p  /  codex exec    │
   └────────────────┬─────────────────┘
                    │ scoped reads
                    ▼
   ┌──────────────────────────────────┐
   │               MFS                │
   │      🗄 Memory  +  🔧 Tools       │
   │   one searchable index over      │
   │   your data — Slack, repos,      │
   │   docs, DBs, files …             │
   └──────────────────────────────────┘
```

## See it in action

Two short demos — each is someone `@OpenClaude`-ing the bot in a Slack thread.

**Delegate a PR review across channels.** A teammate asked for a review in another
channel; from a different channel you tag `@OpenClaude` to handle it. The bot
reads the request from the other channel's history, pulls the PR through MFS, and
reports back — cross-channel context plus reaching an external source (GitHub).

![Open Tag — PR review delegation across channels](https://github.com/user-attachments/assets/6cb1db05-dd12-4a13-a9fa-1a1bf69bcf28)

**Follow up with a new, source-spanning task.** A follow-up in the same thread:
ask the bot to compare two projects and write up the differences. It gathers
context from the indexed sources and produces the document — a same-thread
follow-up that uses external data sources and tools.

![Open Tag — Slack follow-up that spans sources](https://github.com/user-attachments/assets/8f11e931-4248-46c5-b1fb-8128d56b8773)

Together they show what the **Memory + Tools** wiring buys you: from a single
mention, the bot can recall other channels' history and reach external sources
and tools.

## Install the skill

Open Tag ships as the `open-tag-admin` skill. After cloning this repository,
install it globally for Codex:

```bash
npx skills add klovr-co/open-tag --skill open-tag-admin -a codex -g
```

Then open a new Codex task and ask it to set up Open Tag using the
`open-tag-admin` skill. The skill guides the Slack/MFS setup; it does not create
or approve Slack credentials on your behalf.

For the local control panel, clone the repository and create your private
configuration file:

```bash
git clone https://github.com/klovr-co/open-tag.git
cd open-tag
open "OpenTag Setup.command"
```

The guided setup checks local prerequisites and writes a private `.env` with
owner-only permissions. It pauses for the provider actions that only a workspace
administrator can authorize: creating/installing the Slack app, creating the
Zulip Generic bot, and obtaining their credentials. `.env` is ignored by Git and
must never be committed. Once setup finishes, open `OpenTag Control.command` and
select **Start Open Tag**.

## Quick start

Once the skill is installed, you drive the Slack/MFS workflow from Claude Code
or Codex in plain language — the `open-tag-admin` skill guides setup, preflight,
and launch. For a local Slack, Zulip, or dual-transport installation, start with
the guided setup command above. Open your agent in a working directory and ask.

**No credentials yet?** You don't need any tokens in hand first — just say so and
the skill walks you through getting them. The full manual walkthrough (the Slack
app, the two kinds of Slack token, other sources) is under
[Credentials](#credentials) below, folded up. For example:

> I want to run an Open Tag bot but I don't have any Slack credentials yet. Walk
> me through creating the Slack app, turning on Socket Mode, and getting the bot
> and app tokens — tell me which scopes to add and where each token goes.

Once your credentials are ready — or if you already have them in your
environment — drive the rest in plain language:

**1. Give the bot some Memory — make your data searchable:**

> Make my context searchable before I wire up Slack. Index these into MFS:
> 1. my local repo at `/path/to/your/repo`,
> 2. the GitHub repo `your-org/your-repo` — code plus issues and PRs,
> 3. a few Slack channels, just `#eng`, `#support`, `#design`.
>
> The GitHub and Slack tokens are already in my environment, so reference them
> there instead of asking me to paste secrets. Tell me the object/chunk counts
> per source so I can confirm each one indexed.

**2. Stand up the bot:**

> Set up an Open Tag bot using the `claude` backend, listening in my Slack
> channel `#my-team-sandbox`. The Slack tokens are in my environment
> (`SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`). Run the preflight checks, then start
> the bridge once everything looks good.

**3. Inspect or adjust a running bot:**

> What is my Open Tag bot running right now — which backend, which Slack channel,
> and which MFS scopes can it search? Then add `linear://my-workspace` to what it
> is allowed to read.

Then go to Slack and `@OpenClaude` (or `@OpenCodex`) the bot inside a thread — it
gathers context from the permitted MFS scopes and replies in-thread.

### Optional local tools, including Google Workspace CLI

Open Tag passes work to the selected backend with its normal local commands and
skills. For example, an installed, authenticated
[`gws`](https://github.com/googleworkspace/cli) CLI is available to the backend
like any other local tool. Open Tag does not maintain a separate Gmail feature
flag or caller allowlist; the tool's own OAuth grants determine its capabilities.

Open Tag keeps its project-specific skills in `.codex/skills`. This repository
includes only `gws-shared` and `gws-gmail` there; Codex also retains its normal
global `~/.codex/skills` discovery.

```bash
gws auth login -s gmail
```

This does not make Gmail an MFS source or expand `MFS_ALLOWED_SCOPES`. Run the
bot in a trusted channel and use a real sandbox where stronger isolation is
required.

## Use Open Tag in Zulip

Open Tag can also run as a Zulip bot. It keeps the same MFS memory and CLI-agent
backend; only the chat adapter changes. Choose the `native` engine for a fresh,
bounded run on every direct message or stream mention, or the pinned `zulipmcp`
canary for persistent mention-activated stream/topic sessions. Set
`OPENTAG_TRANSPORT` to `both` to keep native Slack and one selected Zulip engine
running together against the same MFS scopes.

1. In Zulip, create a **Generic bot** called **OpenCodex** or **OpenClaude** and
   download its `zuliprc` file. Keep this credentials file outside the Open Tag
   checkout and do not commit it.
2. Subscribe the bot to the stream(s) where it may respond. For a first run, use
   a private sandbox stream.
3. In Open Tag's `.env`, set:

   ```bash
   export OPENTAG_TRANSPORT="both"
   export ZULIP_CONFIG_FILE="/absolute/path/to/opentag-zuliprc"
   export OPENTAG_ZULIP_ENGINE="zulipmcp"
   export OPENTAG_ZULIPMCP_CODEX_PERMISSION_MODE="workspace-write"
   export OPENTAG_ZULIPMCP_MAX_SESSIONS="1"
   export OPENTAG_ZULIPMCP_IDLE_HOURS="0.5"
   export OPENTAG_BACKEND="codex"
   export OPENTAG_WORKDIR="/absolute/path/to/your/workspace"
   export MFS_ALLOWED_SCOPES="file://local/absolute/path/to/your/workspace"
   ```

   ZulipMCP denies private-stream reads by default. To use an explicitly invited
   private sandbox stream, add its exact name:

   ```bash
   export BOT_ALLOWED_PRIVATE_STREAMS="OpenTag Sandbox"
   export BOT_ALLOWED_WRITE_STREAMS="OpenTag Sandbox"
   ```

4. Start **Open Tag Control** and choose **Start Open Tag**, or run:

   ```bash
   python3 scripts/zulip_runtime.py --backend codex
   ```

Then send `@OpenCodex <task>` in an allowed Zulip stream. Native mode supplies up
to 30 current-topic messages to a fresh agent. ZulipMCP starts one persistent
agent per topic, accepts follow-ups without another mention, and exits after its
configured idle wait when the agent follows the canary lifecycle contract. This
is policy-driven rather than an operating-system watchdog. Durable external
context remains limited to
`MFS_ALLOWED_SCOPES` in both modes. The ZulipMCP canary currently handles stream
mentions only; use `native` if Zulip direct-message triggering is required.

The guided setup includes a Zulip preflight: it validates the `zuliprc` structure
and authenticates the bot with Zulip's `GET /api/v1/users/me` endpoint without
printing the API key. Run it after loading `.env`:

```bash
set -a; source .env; set +a
python3 scripts/opentag_doctor.py
```

### Automatically grant access after a mention

This option is supported only by `OPENTAG_ZULIP_ENGINE="native"`. ZulipMCP uses
`BOT_ALLOWED_PRIVATE_STREAMS` and requires the bot to be explicitly invited.

For a workspace where it is appropriate for the Open Tag bot to read every private
channel that explicitly mentions it, enable the following optional mode using a
**separate organization administrator** `zuliprc`:

```bash
export ZULIP_AUTO_GRANT_PRIVATE_HISTORY="true"
export ZULIP_ADMIN_CONFIG_FILE="/absolute/path/to/zulip-admin.zuliprc"
```

When the Open Tag bot is mentioned in a private channel, it enables shared history for
that channel, subscribes the bot, and supplies up to 80 recent messages across
topics in that channel as short-term context. It does not enumerate channels or
grant itself access without a mention. This changes the channel's history policy
for every future subscriber, so use it only where that is the intended policy.

## Credentials

Open Tag uses Slack credentials in **two** different places — don't confuse them:

| Credential | What it's for | Tokens |
|---|---|---|
| **Bridge app** | the bot that receives `@mentions`, reads the thread, posts replies | `SLACK_APP_TOKEN` (`xapp-…`, Socket Mode) **and** `SLACK_BOT_TOKEN` (`xoxb-…`) |
| **Slack-history connector** *(optional)* | indexing channel history into Memory so the bot can recall it | one token — **bot** (`xoxb-…`, recommended) or **user** (`xoxp-…`) |

The skill can do all of this from a plain-language ask. The manual walkthroughs
are here, folded, for when you'd rather do it yourself or want to see exactly
what's being requested. (The skill can guide and diagnose, but it can't bypass
workspace policy — if installing an app needs admin approval, an admin still has
to approve it.)

<details>
<summary><b>1. Create the Slack app + bridge tokens</b> (for receiving @mentions)</summary>

Go to <https://api.slack.com/apps> → **Create New App** → **From scratch**, and
name it for the backend (**OpenClaude** for `claude`, **OpenCodex** for `codex`).

![Slack Create New App button](https://github.com/user-attachments/assets/40ffd973-84d2-483f-beca-720c723223c2)
![Slack Create an app dialog](https://github.com/user-attachments/assets/5119276e-cde3-405e-bd86-7fb33b2218d9)
![Slack From scratch app form](https://github.com/user-attachments/assets/abbdc1cf-012a-44ed-ae62-210abc252980)

1. **Socket Mode** → enable it → create an app-level token with
   `connections:write`. Save it as `SLACK_APP_TOKEN` (`xapp-…`).
2. **OAuth & Permissions** → add Bot Token Scopes:
   - `app_mentions:read` — receive mention events
   - `chat:write` — post and update replies
   - `channels:read` + `channels:history` — read threads in public channels
   - `groups:read` + `groups:history` — private channels (optional)
3. **Event Subscriptions** → subscribe to the bot event `app_mention`.
4. **Install to Workspace** (reinstall after any scope/event change) → copy the
   **Bot User OAuth Token**. Save it as `SLACK_BOT_TOKEN` (`xoxb-…`).
5. Invite the bot to your sandbox channel: `/invite @OpenClaude`.

A private channel needs the bot to actually be a member, even with
`groups:history`. If preflight reports `not_in_channel`, invite it again.

</details>

<details>
<summary><b>2. Index Slack history into Memory</b> — bot token vs user token (optional)</summary>

This is separate from the bridge: it's the MFS **slack connector**, which indexes
channel history so the bot can search past conversations. You need **one** token:

- **Bot token** (`xoxb-…`, recommended) — same app as above; under **OAuth &
  Permissions** add `channels:read`, `channels:history`, `users:read` (plus
  `groups:*` for private channels), install, and copy the `xoxb-…` token. Invite
  the bot to any private channel you want indexed.
- **User token** (`xoxp-…`) — created the same way under **User Token Scopes**.
  Use it only when the bot identity can't reach what you can (DMs, channels the
  bot isn't in); always pair it with a channel allowlist so it doesn't index the
  whole workspace.

You don't write the connector config by hand — the **mfs-ingest** skill does,
keeping the token as an `env:` reference and bounding the channels. Full details
and screenshots are in the [MFS Slack connector documentation](https://github.com/zilliztech/mfs/blob/main/docs/connectors/slack.md).

</details>

<details>
<summary><b>3. Other data sources</b> (GitHub, Postgres, Linear, …)</summary>

Each connector has its own credential, and the **mfs-ingest** skill walks you
through getting each one — what's needed and where to obtain it (a GitHub PAT, a
Postgres DSN, an OAuth token, …) — then stores it as an `env:` / `file:`
reference, never inline. Open Tag itself only *consumes* what MFS has indexed.

The full per-connector walkthroughs (20+ sources) are in the
[MFS connector documentation](https://github.com/zilliztech/mfs/tree/main/docs/connectors/).

</details>

## Where it stands vs. a hosted tag bot

What Open Tag leans on — and where its edge is — is **Memory breadth**: MFS exposes
20+ connectors, including raw data layers (Postgres / Mongo / BigQuery / S3),
trackers (GitHub / Jira / Linear), chat, and local files, all **self-hosted**, so
your data and credentials never leave your machines. A hosted tag bot wins on the
things a demo deliberately skips: managed zero-ops, enterprise governance
(approvals, audit, spend limits), an ambient proactive mode, and an org-level
identity model.

So this is a demo/reference implementation, **not a production security
boundary**. It has no hardened sandbox, multi-user policy engine, audit system, or
approval flow. Anyone who can mention the bot can drive the backend, which runs
with your shell and environment — use it in an **isolated Slack channel on a
non-production machine** while adapting the pattern.

## What the skill sets up for you

You don't run any of this by hand. From the plain-language asks above, the
`open-tag-admin` skill drives the whole chain on your machine:

- gets the MFS server running on `127.0.0.1:13619` (installing it if needed);
- indexes the sources you name into MFS — the bot's searchable **Memory**
  (the indexing itself is handled by the **mfs-ingest** skill);
- runs the preflight checks and starts the Slack bridge.

What you bring is what an agent can't do for you:

- a machine to run on — the backend runs locally with your shell and environment;
- a Slack workspace where you can create and install an app, or an admin who can
  approve it;
- access to whatever data you want indexed.

The runnable code is in this repository — the skill handles the workflow, but
you can also run the steps yourself:

<details>
<summary>The manual sequence the skill automates</summary>

```bash
# 1. install + run MFS
uv tool install mfs-server && mfs-server run            # binds 127.0.0.1:13619

# 2. index your Memory (or just let the mfs-ingest skill do it)
mfs add /path/to/your/repo
mfs add slack://team-memory --config ./slack.toml       # optional Slack history

# 3. point the bot at the permitted scopes + Slack app + backend
export MFS_ALLOWED_SCOPES="file://local/path/to/your/repo,slack://team-memory"
export SLACK_APP_TOKEN="xapp-…"  SLACK_BOT_TOKEN="xoxb-…"
export OPENTAG_BACKEND="claude"  SLACK_CHANNEL_ID="C0…"
export MFS_URL="http://127.0.0.1:13619"  MFS_TOKEN="$(cat ~/.mfs/server.token)"

# 4. preflight, then start the bridge
cd open-tag
python scripts/opentag_doctor.py --channel-id "$SLACK_CHANNEL_ID"
uv run --with slack-bolt python scripts/slack_socket_agent.py --backend "$OPENTAG_BACKEND"
```

Each step is documented in [`references/`](references/) (Slack
adapter, backends, runtime agent, memory).

</details>
