---
name: open-tag-admin
description: Admin/control console for an Open Tag Slack workflow backed by MFS. Use to set up a new Open Tag bot from scratch, check what is currently running (backend and permitted MFS scopes), change settings, add or remove data sources, switch the CLI agent backend (claude -p / codex exec), run preflight checks, and troubleshoot thread context, retrieval, or task execution.
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

1. Run `tag setup` to authorize Slack CLI, create or link an app, select one or
   more joined sandbox channels, and approve selected-channel history indexing.
2. Let setup validate Socket Mode, bot, and history credentials separately.
3. Start the Open Tag bridge.
4. Mention the bot in a Slack thread.
5. Let the bridge invoke the selected backend with thread context and scoped MFS
   helper scripts for permitted external context.

This skill does not call a model API directly. Model access, tool access, and
write permissions come from the selected CLI agent backend.

## Prerequisites

Open Tag requires a **running MFS server with at least one indexed source**.
Slack-first onboarding configures that source itself:

1. **MFS server installed and running.** `uv tool install mfs-server`, then
   `mfs-server run` (binds `127.0.0.1:13619`). Check with
   `curl -s 127.0.0.1:13619/healthz`.
2. **At least one data source indexed.** `tag setup` writes and registers a
   Slack connector limited to the explicitly selected channels. Use the
   **mfs-ingest** skill only for additional non-Slack sources.

`opentag_doctor.py` fails fast with a hint if either is missing.

## Bot name convention

The default Slack identity is **Tag**, so teammates use `@Tag <task>`
regardless of whether Codex or Claude Code is configured underneath. Slack
routes the mention by bot user ID, and Tag strips the mention before invoking
the backend. Set `OPENTAG_BOT_NAME` if your Slack app uses another display name.

## Setup and management workflow

Start every setup, change, or recovery request by inspecting what already exists.
Use the installed `tag` CLI; in a source checkout use `./tag` with an isolated
absolute `TAG_HOME`. Do not drive the interactive menu by feeding numbered input.
Read `docs/tag-management.md` for the command/output contract.

1. If Tag is not installed, run the repository's `./install.sh` (Windows:
   `./install.ps1`) and use the printed launcher. Installation creates a private
   persistent home and runtime; configuration is `config/settings.json` there,
   not `.env` in the source checkout.
2. Run `tag inspect --json`. Read `state`, `configuration.fields`, `backend`,
   `services`, and `next_command`. Use `--offline` for a local-only inspection.
   Do not ask for values already saved. Redacted `[set]` values are present,
   not missing.
3. For initial setup, run `tag setup` in the user's terminal. It seeds only
   missing defaults, invokes the real Slack CLI authorization flow when needed,
   and pauses before app creation/linking and history indexing. Never ask the
   operator to send tokens in chat: setup uses hidden prompts. It accepts an
   existing app by App ID and exposes an action to open that app's settings.
   Its multi-select picker accepts joined channels only and persists their
   stable IDs.
4. Use `tag config keys --json` to discover supported keys, and
   `tag config set KEY VALUE --json` for each requested nonsecret change. Preserve
   unrelated settings. Use the mfs-ingest skill for additional sources; the
   Slack source created by setup is already selected-channel scoped.
5. Codex is the default. Change the runtime agent only when requested using
   `tag config set OPENTAG_BACKEND claude --json` (experimental) or `codex`.
   The assistant performing installation and Tag's runtime backend are separate.
6. Run `tag doctor --json` to diagnose failed checks. If local MFS is stopped,
   `tag start` starts it before preflight. Do not repeatedly rewrite settings to
   fix a stopped service. Installed executables do not prove authentication;
   guide sign-in through the backend's own interface when needed.
7. When startup is part of the request, run `tag start`, then `tag status --json`.
   Settings changes take effect on the next start; restart a running deployment
   only when the requested change calls for it. Verify a real mention and reply
   in the permitted Slack channel before claiming end-to-end success. JSON
   inspection deliberately reports `first_reply: not_verified`.

For returning users, use the same inspect/change/verify loop. For failures, read
structured checks and their next actions, then inspect logs if necessary. Never
paste unreviewed logs into chat because third-party output can contain secrets.
`tag setup` is the human alternative: saved valid answers are skipped, Ctrl-C
pauses, and another run resumes. Invalid JSON requires repair, not replacement.

## Adding data sources

Open Tag's reach is exactly what MFS has indexed and what you list in
`MFS_ALLOWED_SCOPES`. `tag setup` owns the primary Slack-history connector. To
add a different source, use the **mfs-ingest** skill; it handles that source's
credentials and connector configuration.

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
- `opentag_process_env.py`: keep Socket Mode and bridge access-control settings
  out of backend processes.
- `opentag_agent.py`: build a non-interactive prompt and invoke the selected CLI
  backend.
- `mfs_search.py` and `mfs_cat.py`: call the MFS HTTP API with scoped search and
  reads.
- `opentag_doctor.py`: preflight environment variables, Slack bot access, MFS
  reachability, allowed scopes, and backend availability.

Shell scripts can wrap these commands, but Python is less brittle for Slack Web
API calls, JSON handling, temporary files, subprocess timeouts, and cross-agent
backend selection.

## Runtime Contract

The Slack bridge invokes a fresh CLI agent per mention. That runtime agent must
follow `references/runtime-agent.md`. Keep runtime behavior there, not in this
admin skill.

Slack access is owner-only by default. `SLACK_ALLOWED_USER_IDS` must contain at
least the owner's member ID; add comma-separated IDs only on explicit owner
request. Never use first-mention claiming or leave this setting empty.

Thread context is short-term state. Durable context should come from permitted
MFS scopes such as indexed Slack history, repos, docs, issues, databases, object
stores, or web sources. See `references/memory.md` for the retrieval model.

Never hard-code real workspace names, channel IDs, user IDs, local absolute
paths, or customer/project details into this skill. Use placeholders in
documentation and environment examples.

## References

- Read `references/slack-adapter.md` when configuring or running Slack.
- Read `references/backends.md` when changing backend selection or command
  invocation.
- Read `references/runtime-agent.md` when changing per-mention behavior.
- Read `references/memory.md` when changing the MFS retrieval model.
