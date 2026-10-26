<!-- Modified by klovr.co in 2026 for Tag. See NOTICE and repository history. -->

# Runtime Agent Contract

This file describes the behavior contract for the CLI agent launched by the
bridge. The agent does not read it. It receives a lean set of standing
instructions from `build_instructions` in `scripts/opentag_agent.py`, and loads
procedures needed only by some requests from the runtime skills in
`runtime-skills/skills/` (a Claude plugin and a Codex skill root):

| Skill | Use |
| --- | --- |
| `tag-files` | Images, disposable artifacts, lasting files and their Slack delivery |
| `tag-attachments` | Earlier thread files, earlier returned images, archives |
| `tag-memory` | Remember, change, forget, or list saved memory |
| `tag-slack-search` | Named-channel and all-Slack search scope; channel summaries |
| `tag-slack-actions` | Posting a channel message; creating a Canvas |
| `tag-handoff` | Asking other Tags, when the Tag may |

Keep always-on rules (safety, answer format, never claiming unconfirmed
success) in the standing instructions, and procedures in skills.

## Mental Model

- **Brain**: the current CLI agent process. It receives the Slack thread, the
  request's search grant, and the workspace. Unless a backend provides its own
  session continuity, each invocation is a fresh run.
- **Memory**: facts people explicitly asked Tag to remember, loaded into every
  prompt for the current channel (see `references/memory.md`). Older Slack
  messages are not memory: the agent reads them live with `slack_search.py`
  when a request needs them, and nothing it reads is saved (ADR 0012).
- **Tools**: any command, skill, or file tool available to the backend in the
  workspace.
  A tool's own credentials and grants determine its capabilities; Tag does not
  add per-tool caller allowlists.

## Runtime Inputs

- Slack channel id.
- Current Slack thread text.
- The request's Slack search grant (`OPENTAG_SLACK_SEARCH_GRANT`) and, for
  requests from Slack, its `action_token` (`OPENTAG_SLACK_ACTION_TOKEN`).
- Helper script paths.
- Backend workspace directory.

## Core Workflow

1. Identify the user's current request from the Slack thread.
2. Use earlier messages in the thread to resolve follow-up references such as
   "that connector", "the previous answer", or "do the same for X".
3. Search Slack only when the request needs earlier messages:
   `slack_search.py search "<query>"` reads the current conversation live,
   threads included. Results link to each message; search again rather than
   relying on an earlier search.
4. When the user asks to summarize the current Slack channel, its history, or
   its next steps, do not treat the current thread as the whole channel. Read it
   with `slack_search.py history --days 7` (or the period the user names). Treat
   a bare “summarise this” or “summarize this” as a channel-summary request when
   the current thread contains no substantive context beyond the request or
   working status reply.
5. For explicit task requests, run commands or edit files inside the configured
   workspace using the CLI backend's normal tools. Keep changes scoped and
   summarize verification.
6. Understand Slack search scope from the user's request as part of normal tool
   selection (the `tag-slack-search` skill). Repeat `--channel NAME` for
   specifically requested channels, passing names exactly as written and never
   silently correcting a rejected name. Use `--all` only for clear wording such
   as `across Slack` or `all channels`; it adds Slack's public search.
   Conflicting or fragmentary wording such as `search general workspace all`
   requires a short clarification without calling a search helper. Preserve the
   helper's source-channel attribution.
7. If public search is unavailable (no `action_token`, a guest, the rate limit,
   or a missing permission), the helper says why; pass that on plainly.
8. Ground answers about Slack in what the helper returned.
9. Return only the final Slack-ready answer.

When a Slack user explicitly asks for a message to be posted, sent, or shared
in the current channel, use the channel-post helper supplied in the runtime
prompt. It creates a new top-level channel message and is restricted to the
current channel. Do not post a message merely because you created a summary.

## Answer Contract

- Ground source claims in Slack messages, files, or tool output, or clearly
  label them as inference.
- Do not add a `Sources:` section by default. Cite only when the user asks for
  sources/citations or when provenance materially helps the answer.
- When citing, link Slack messages with the helper's links, and files with paths
  and line ranges, for example `scripts/slack_search.py lines 50:103`.
- For commands the user explicitly requested, report the observed result. Omit
  internal helper commands and empty stdout/stderr details.
- For code-writing tasks, summarize changed files and verification commands.
- Keep the answer concise enough for a Slack thread.

## Boundary Model

Tag relies on the selected backend process boundary and the workspace
permissions granted by the operator. Production deployments should add a real
sandbox, explicit tool allowlists, and auditable data-source policies.

- Context boundary: the Slack bridge passes the current thread, channel id,
  and the request's search grant.
- Data boundary: `slack_search.py` reads only the current conversation and the
  channels in the bridge-generated grant: channels the person named in which
  both they and the Tag are members, and public search only when the request
  text asks for all channels. It stores nothing it reads.
- Intent boundary: the runtime agent interprets which search scope the user
  requested; the helper refuses anything outside the grant.
- Execution boundary: the backend runs with the permissions used to start the
  bridge. Use a trusted workspace for demos and a sandbox for production.
- Memory boundary: durable context is Tag memory for the current channel and
  all channels. Provider-native memory is disabled for Tag runs.
- Tool boundary: locally installed commands and skills run with the permissions of
  the backend process. Their own credentials and authorization grants apply. Run the
  bot in a trusted channel and use a real sandbox for stronger isolation.

## Saved artifact locations

Save new requested durable deliverables in the current channel's artifact folder
provided by the runtime prompt, unless the user specifies a different workspace
path. Edit existing files in place. Resolve unqualified filenames against the
current channel's artifact folder and the workspace root for legacy files; ask
if ambiguous. Do not relocate old files or search other channels' folders just
to match a filename. These folders organize files; they are not a filesystem
authorization boundary. Temporary image delivery and disposable invocation
artifacts continue to use the runtime's temporary result directories.
