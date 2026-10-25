# CLI Backends

Tag treats the backend as the Brain: a non-interactive CLI process that can
read the prompt, use the workspace, call MFS helpers, and return a Slack-ready
answer. Choose the backend explicitly for each deployment.

## Built-In Backend: Claude Agent SDK

Use this backend when the operator has a working `claude` CLI session or a
Claude [API connection](../docs/reference/api-connections.md):

```bash
python scripts/opentag_agent.py \
  --backend claude \
  --event-stream \
  --question "Summarize this thread and list the next action." \
  --channel-id "$SLACK_CHANNEL_ID" \
  --thread-file /tmp/thread.txt \
  --workdir /path/to/repo
```

The Slack bridge runs each request as a Claude Agent SDK session
(`scripts/claude_agent_backend.py`) and resumes the same session for later
mentions in that Slack thread (see the event contract below). It uses the operator's `claude` executable
when present, `--add-dir` access to the skill and attachment directories, the
`auto` permission mode, and the workspace `.mcp.json` servers. The session
emits the same normalized events as Codex App Server: final-answer deltas,
sanitized activity, Slack approval requests, and terminal status. Slack Stop
interrupts the session, and the Tag's model, thinking, and Fast Mode defaults
come from the signed-in account's model catalog. See
[ADR 0008](../docs/adr/0008-claude-agent-sdk.md).

Each Tag sets Claude's permission mode with `OPENTAG_CLAUDE_PERMISSION_MODE`
(Tag.app's Details → **Permissions** → Claude, or `tag NAME settings ai
permissions claude-mode=MODE`): `auto` (default), `acceptEdits`, `default`,
`dontAsk`, or `bypassPermissions`. Codex has its own settings, passed to App
Server `thread/start` and `thread/resume`: `OPENTAG_CODEX_SANDBOX` (`sandbox`,
default `workspace-write`), `OPENTAG_CODEX_APPROVALS` (`approvalPolicy`; empty
or unset sends nothing, so Codex follows the operator's own configuration), and
`OPENTAG_CODEX_REVIEWER` (`approvalsReviewer`, default `auto_review`; forced to
`user` when the approval policy is `never`, since nothing is asked). Unknown
values stop the request instead of loosening access; the status report shows
them with the reason instead of failing. The rollback transports
(`OPENTAG_CODEX_TRANSPORT=exec`, `OPENTAG_CLAUDE_TRANSPORT=print`) have fixed
flags, so they refuse any Codex setting, and `claude -p` runs only when the
Claude mode is `bypassPermissions`, since it always skips permission checks.
The SDK loads the `user`, `project`, and `local` setting sources, so the
operator's `~/.claude/settings.json` and the Tag workspace's
`.claude/settings.json` and `.claude/settings.local.json` apply. Claude checks
hooks, then deny rules, ask rules, the mode, and allow rules, and only then asks
in Slack; deny and ask rules hold even in `bypassPermissions`. Anything Claude would ask about is
sent privately to the requester. Every request offers Allow once, Deny, and
Deny and stop. When the CLI suggests allow rules or directories, Tag also
offers Allow for this task (`session` destination) and Always allow
(`localSettings`, the Tag workspace's `.claude/settings.local.json`). Plain
shell commands also offer Always allow this prefix, a `Bash(<program> <first
argument>:*)` rule like Codex's prefix amendments. The bridge confirms saved
rules before applying them.

Each Tag follows four sets of rules for both agents: its own (Tag.app's
**Rules** and `tag NAME settings ai permissions allow|block|remove`), other
rule files in its folder, the installation's rules for all Tags (`tag settings
ai rules …`, kept in `shared/permissions/`), and the operator's own. Tag only
reads the folder's other files and the operator's own rules. Claude's
own Tag rules are the workspace's `.claude/settings.local.json`; the all-Tags
ones reach the SDK as `settings` (and `claude -p` as `--settings`), which add
to the user, project, and local sources, together with deny rules
`Edit(./.claude/settings.local.json)` and `Edit(./.claude/settings.json)` so
Claude's file tools can't change its own rules (a shell command still could). Tag refuses to start Claude when that file,
the Tag's own, the folder's `.claude/settings.json`, or the operator's
`~/.claude/settings.json` can't be read, since Claude would skip it. Codex's own Tag rules are the workspace's
`.codex/rules/tag.rules`, and before each run Tag copies the all-Tags rules to
`.codex/rules/all-tags.rules` beside them. Tag starts Codex with
`-c projects={"<workspace>"={trust_level="trusted"}}`, which makes Codex 0.162
and later add that folder's `.codex/rules` to `$CODEX_HOME/rules` (Codex's own
sandbox keeps `.codex` read-only). Codex's Always allow and host decisions
would write to `$CODEX_HOME/rules/default.rules`, so Tag answers Codex with a
plain approval or denial and writes the rule to the Tag's file itself. Codex
reads that file when a thread starts, so until then Tag answers plain commands
that match a prefix saved in the same run, and network requests for a host saved
in the same run, unless a rule Codex reads asks about or blocks them, or one
Tag can't parse. If those Codex rules block or
ask, Tag checks `config/read` before each thread and stops when Codex reports
the Tag's layer disabled, as older versions do. The `exec` rollback refuses
such rules. Rules Tag can't parse count as asking, so they trigger that check
too. Tag edits only lines that hold just the rule being changed, and saves
under a system file lock, freed if Tag stops, with a uniquely named temporary
file.

Tag keeps Codex rules and trust only in a Tag's own default folder. A Tag whose
`OPENTAG_WORKDIR` points at a folder of the operator's gets neither, since they
would also change the operator's own Codex sessions there: Always allow offers
no saved Codex rule, `permissions allow|block|remove codex` is refused, and a
run is refused while the all-Tags Codex rules block or ask, or can't be read, or
while that folder's own Codex rules block or ask and Codex hasn't been told to trust the folder.

Set `OPENTAG_CLAUDE_TRANSPORT=print` to roll back to the previous print mode:

```bash
claude -p \
  --dangerously-skip-permissions \
  --add-dir <workdir> \
  --add-dir <skill-dir> \
  <prompt>
```

Print mode forwards top-level text deltas and the final result only; it has no
activity rows, approvals, Stop confirmation, or settings control.

## Built-In Backend: Codex Exec

Use this backend when the operator has a working `codex` CLI session and wants
the runtime agent to inspect files, call MFS helpers, run tests, prepare code
changes, or execute explicitly requested workspace tasks.

```bash
python scripts/opentag_agent.py \
  --backend codex \
  --question "Where is the Slack connector implemented? Cite sources." \
  --channel-id "$SLACK_CHANNEL_ID" \
  --thread-file /tmp/thread.txt \
  --workdir /path/to/repo
```

The runner invokes:

```bash
codex exec \
  --dangerously-bypass-approvals-and-sandbox \
  -c shell_environment_policy.inherit=all \
  -C <workdir> \
  --add-dir <skill-dir> \
  --add-dir <memory-root> \
  --skip-git-repo-check \
  --output-last-message <tmp-output-file> \
  <prompt>
```

Use this only in a trusted workspace. It intentionally mirrors a high-autonomy
Slack agent demo, not a locked-down production deployment.

No `OPENAI_API_KEY` is required by this skill path. The backend uses the local
Codex CLI session and inherits the environment needed for MFS.

When Slack streaming is enabled, Tag invokes `codex exec --json` and reads
the completed `agent_message` event. The current CLI does not expose answer
token deltas, so Tag shows Slack's native loading indicator and posts the
complete response without a fake typewriter animation.

## Backend Selection

- Use the backend that is already authenticated and approved for the operator's
  workspace.
- Keep the Slack bridge thin: backend-specific behavior belongs in
  `opentag_agent.py`, not in Slack event handling.
- Keep the normalized event contract backend-neutral (`status`, `delta`,
  `final`, `error`, plus the richer `message_*`, `activity_*`,
  `approval_request`, `run_info`, `usage`, `session`, `context`, and `turn_complete` events); chat transports
  must never parse backend-native event payloads.
- Report thinking as activity: an `activity_start`/`activity_complete` pair
  labelled `THINKING_LABEL` (`scripts/agent_activity.py`) whose completed
  `details.output` is the provider's thinking summary, never raw reasoning.
  Claude asks the CLI for `--thinking-display summarized` and maps thinking
  content blocks; Codex sends turn `summary: "auto"` (`"none"` when disabled, so a resumed thread stops too) and maps `reasoning`
  items' `summary`. Both stop when `OPENTAG_SHOW_THINKING=0`.
- Receive the thinking level as the backend-neutral `reasoning_effort`. The
  Tag's default level (`OPENTAG_DEFAULT_EFFORT`) is applied once, in
  `agent_models.discover_tag_models`, to the default model's default level, so
  Codex gets it as the App Server turn `effort` and Claude as the Agent SDK
  `effort` option without either adapter knowing about it.
- Emit `run_info` with the concrete model the backend actually used, even when
  Tag requested an alias or the account default. Codex reports it from
  `thread/start`; Claude reports the main thread's assistant model.
- Emit `usage` with a cumulative `usage` snapshot containing `input_tokens`,
  `output_tokens`, and `total_tokens`. Input includes cache reads/writes; optional
  cache and reasoning counts are subsets, never added twice. Codex maps
  `thread/tokenUsage/updated.tokenUsage.total`; Claude maps the SDK result's
  `usage`. The runner accumulates reported usage across retry attempts; duplicate
  snapshots replace earlier snapshots within an attempt. Missing usage is unknown.
- Finish a request only when the agent's background sub-agents have finished.
  A turn that ends with sub-agents still running is not the answer: its text is
  commentary, the session stays open within the request's maximum runtime, and
  `turn_complete` comes from a later turn that uses their results. Claude wakes
  itself for that turn when each agent reports back (`task_*` system messages).
  Codex never wakes the main thread, so the adapter tracks `subAgentActivity`
  items and starts the follow-up turn itself once they all finish. Sub-agent
  threads' own messages and turn events never reach Slack.
- Emit `session` with the conversation id the request continued or started,
  and accept it back as `--resume-session`. The bridge stores it per Slack
  thread and backend, so later mentions resume the same Codex thread or Claude
  session. A missing conversation is replaced, not fatal. Text-only reply
  summaries never resume or report a session. Emit `context` with the
  conversation's size at the latest main-thread model call; the bridge starts a
  fresh conversation once it reaches `OPENTAG_THREAD_MAX_CONTEXT_TOKENS` or sits
  idle `OPENTAG_THREAD_IDLE_HOURS`. Pass standing instructions outside the
  conversation (Codex `developerInstructions`, Claude system prompt) and send a
  continued conversation only the thread's new messages. See
  [ADR 0009](../docs/adr/0009-thread-scoped-agent-conversations.md).
- Keep standing instructions lean and register Tag's runtime skills
  (`runtime-skills/`) so procedures load only when a request needs them: Claude
  loads the folder as a local plugin (`--plugin-dir`, or the SDK `plugins`
  option, with its `Skill(tag:…)` calls allowed), and the Codex App Server adds
  `runtime-skills/skills` with `skills/extraRoots/set`. The instructions also
  name each skill's file, for transports that cannot register them.

Generated images use a file handoff rather than a new stream event. For each
Slack invocation, the prompt names a temporary `results/images` directory. A
backend places only final PNG, JPEG, GIF, or WebP files there; after a successful
run, the Slack bridge validates and uploads them to the originating thread. The
directory is deleted when that invocation finishes.


### Activity reply summaries

After successful Slack delivery, the bridge queues `agent_summary` with the
completed answer and selected backend, using the reported model when available.
Normalized `run_info` also saves the request's actual model and display label to
Activity for both backends. Missing historical model data remains absent. A separate bounded background run
produces a one-sentence TL;DR; it never resumes or repeats the Slack task. Both
Codex App Server and Claude Agent SDK run this text-only pass in a temporary
working directory with tools and user hooks disabled. Existing provider sign-in
is reused. Only final-answer events from a completed turn become the sanitized
`reply_summary` cache; commentary, partial turns, and errors never do. Failure
leaves the locally generated `reply_preview`. CLI JSON and Tag.app read the same
cache. Existing installations receive this through the runtime manifest without
new settings or permissions; historical answer text is not available to backfill.
## API connections and local usage

Both rich transports accept explicit per-Tag API keys and base URLs. Codex also
supports Azure Responses deployments. See [API connections](../docs/reference/api-connections.md)
for settings, authentication precedence, model catalogs and limitations.

Adapters normalize provider usage as `usage` events with cumulative per-scope
`input_tokens`, `output_tokens`, `cached_input_tokens`, `cache_creation_tokens`,
and `reasoning_output_tokens` where available. Input includes cache reads and
writes; reasoning is included in output. Unknown counts remain null. Claude may
also provide `cost_usd`, an estimate. `scope_id` identifies a cumulative SDK
session or Codex thread; repeated snapshots replace prior totals for that scope.
The runner consumes these events into the private ledger before the Slack bridge,
so the bridge never parses native usage or branches on the provider. Interrupted
runs with no provider usage remain unknown. `tag usage` reports monthly totals
and an advisory budget; no hard spending enforcement is implemented.
