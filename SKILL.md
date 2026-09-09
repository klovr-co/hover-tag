---
name: open-tag-admin
description: Admin/control console for an Open Tag Slack and Zulip tag-in workflow backed by MFS. Use to set up a new Open Tag bot from scratch, check what is currently running (backend, permitted MFS scopes, and selected chat transport), change settings, add or remove data sources, switch the CLI agent backend (claude -p / codex exec), run preflight checks, and troubleshoot thread context, retrieval, or task execution.
---

<!-- Modified by klovr.co in 2026 for Tag. See NOTICE and repository history. -->

# Open Tag (admin)

This skill is the **control console** for an Open Tag deployment. Use it for the
first-time setup and for ongoing operation alike: inspect the live bot, change
the backend or permitted scopes, add a new data source, move the bot to another
Slack channel, or debug a run.

Keep the architecture generic:

- **Brain**: the selected CLI agent backend — `claude -p` (Claude Code) or
  `codex exec` (Codex).
- **Memory**: MFS-indexed, operator-authorized context such as Slack history,
  repositories, docs, issues, databases, or object stores.
- **Tools**: MFS connectors for external read/search plus any explicit tools the
  backend is allowed to use in the workspace.

The user-facing flow is:

1. Configure MFS sources and allowed scopes.
2. Configure a Slack app with Socket Mode and/or a Zulip Generic bot, then add it to a sandbox channel or stream.
3. Start the Open Tag bridge.
4. Mention the bot in a Slack thread or Zulip stream topic.
5. Let the bridge invoke the selected backend with thread context and scoped MFS
   helper scripts for permitted external context.

This skill does not call a model API directly. Model access, tool access, and
write permissions come from the selected CLI agent backend.

## Prerequisites

Open Tag is a thin layer on top of a **running MFS server with at least one
indexed source**. Confirm these before any Slack work:

1. **MFS server installed and running.** `uv tool install mfs-server`, then
   `mfs-server run` (binds `127.0.0.1:13619`). Check with
   `curl -s 127.0.0.1:13619/healthz`.
2. **At least one data source indexed.** Open Tag only *consumes* already-indexed
   scopes as Memory — it does not configure connectors itself. Use the
   **mfs-ingest** skill (Codex: `$mfs-ingest`) to add a source; see
   "Adding data sources" below.

`opentag_doctor.py` fails fast with a hint if either is missing.

## Bot name convention

The default Slack identity is **OpenMax**, so teammates use `@OpenMax <task>`
regardless of whether Codex or Claude Code is configured underneath. Slack
routes the mention by bot user ID, and Tag strips the mention before invoking
the backend. Set `OPENTAG_BOT_NAME` if your Slack app uses another display name.

## Setup Workflow

1. Satisfy **Prerequisites** above (MFS running + at least one indexed source).
2. For a new local installation, run `./install.sh`. It verifies pinned local
   prerequisites, writes a private `.env`, and leaves provider credentials to
   the workspace administrator. Use `slack-app-manifest.yaml` to create the
   Slack app.
3. For Slack, read `references/slack-adapter.md`, confirm an isolated channel,
   then create/install the Socket Mode app and invite it to that channel.
4. For Zulip, create a Generic bot, subscribe it to the intended stream, and
   set `ZULIP_CONFIG_FILE` to its downloaded `zuliprc` file. Choose
   `OPENTAG_ZULIP_ENGINE=native` for one-shot DM/mention runs or `zulipmcp` for
   persistent stream/topic sessions. Never run both Zulip engines for one bot.
5. Configure MFS memory sources and set `MFS_ALLOWED_SCOPES` to the exact source
   roots the runtime agent may use.
6. Choose `OPENTAG_BACKEND` explicitly: `claude` or `codex`.
7. Run `python scripts/opentag_doctor.py --channel-id <channel-id>` (the channel
   ID is needed only for Slack) and fix any failed check.
8. Start the configured transports with `./tag start`. Use `./tag status` and
   `./tag logs` to inspect it. The command prints a
   "what's live now" summary — read it, then validate thread context,
   permitted-context retrieval, and task execution with a realistic delegated task.

## Adding data sources

Open Tag's reach is exactly what MFS has indexed and what you list in
`MFS_ALLOWED_SCOPES`. To add a source, use the **mfs-ingest** skill — it handles
credentials and writes the connector config; Open Tag never duplicates that.

Representative sources (each is `mfs add <uri> --config <toml>` once, then add
its root to `MFS_ALLOWED_SCOPES`):

- **Local repo / docs**: `mfs add /path/to/repo` → `file://local/path/to/repo`
- **Slack history**: `slack://team-memory` (own token + channel allowlist)
- **GitHub (code + issues)**: `github://your-org/your-repo`
- **Linear (issues)**: `linear://your-workspace`
- **Postgres rows**: `postgres://prod`

MFS supports 20+ connectors (databases, object stores, trackers, chat, web).
For the full list and per-connector credentials, point users at the
**mfs-ingest** skill and `docs/connectors/`. This breadth of Memory — including
raw data layers, all self-hosted — is Open Tag's main edge over a hosted tag bot;
it does **not** add hosted governance, audit, or approval flows.

## What The Python Scripts Do

Keep the Python scripts as deterministic glue:

- `slack_socket_agent.py`: receive Slack `app_mention`, read the thread, post
  progress, call the backend, and post the final answer.
- `zulip_runtime.py`: expose the Zulip engine seam and launch either the native
  adapter or the pinned ZulipMCP adapter with runtime-generated policy files.
- `zulipmcp_entrypoint.py`: apply the canary session cap before starting the
  upstream listener.
- `opentag_process_env.py`: prevent one transport's credentials from entering
  the other transport's backend processes.
- `opentag_agent.py`: build a non-interactive prompt and invoke the selected CLI
  backend.
- `mfs_search.py` and `mfs_cat.py`: call the MFS HTTP API with scoped search and
  reads.
- `opentag_memory.py`: maintain optional local seed notes and re-index them in
  MFS for deterministic demos.
- `opentag_doctor.py`: preflight environment variables, Slack bot access, MFS
  reachability, allowed scopes, and backend availability.

Shell scripts can wrap these commands, but Python is less brittle for Slack Web
API calls, JSON handling, temporary files, subprocess timeouts, and cross-agent
backend selection.

## Runtime Contract

The Slack bridge invokes a fresh CLI agent per mention. That runtime agent must
follow `references/runtime-agent.md`. Keep runtime behavior there, not in this
admin skill.

Thread context is short-term state. Durable context should come from permitted
MFS scopes such as indexed Slack history, repos, docs, issues, databases, object
stores, or optional local seed notes. See `references/memory.md` for the optional
helper's file shape.

Never hard-code real workspace names, channel IDs, user IDs, local absolute
paths, or customer/project details into this skill. Use placeholders in
documentation and environment examples.

## References

- Read `references/slack-adapter.md` when configuring or running Slack.
- Read `references/backends.md` when changing backend selection or command
  invocation.
- Read `references/runtime-agent.md` when changing per-mention behavior.
- Read `references/memory.md` only when using optional local seed notes.
