# CLI Backends

Tag treats the backend as the Brain: a non-interactive CLI process that can
read the prompt, use the workspace, call MFS helpers, and return a Slack-ready
answer. Choose the backend explicitly for each deployment.

## Built-In Backend: Claude Agent SDK

Use this backend when the operator has a working `claude` CLI session:

```bash
python scripts/opentag_agent.py \
  --backend claude \
  --event-stream \
  --question "Summarize this thread and list the next action." \
  --channel-id "$SLACK_CHANNEL_ID" \
  --thread-file /tmp/thread.txt \
  --workdir /path/to/repo
```

The Slack bridge runs one Claude Agent SDK session per request
(`scripts/claude_agent_backend.py`). It uses the operator's `claude` executable
when present, `--add-dir` access to the skill and attachment directories, the
`auto` permission mode, and the workspace `.mcp.json` servers. The session
emits the same normalized events as Codex App Server: final-answer deltas,
sanitized activity, Slack approval requests, and terminal status. Slack Stop
interrupts the session, and per-user model, thinking, and Fast Mode choices
come from the signed-in account's model catalog. See
[ADR 0008](../docs/adr/0008-claude-agent-sdk.md).

`OPENTAG_CLAUDE_PERMISSION_MODE` selects `auto` (default), `acceptEdits`,
`default`, `dontAsk`, or `bypassPermissions`. Anything Claude would ask about is
sent privately to the requester as a one-time approval.

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
  `approval_request`, `run_info`, and `turn_complete` events); chat transports
  must never parse backend-native event payloads.
- Emit `run_info` with the concrete model the backend actually used, even when
  Tag requested an alias or the account default. Codex reports it from
  `thread/start`; Claude reports the main thread's assistant model.

Generated images use a file handoff rather than a new stream event. For each
Slack invocation, the prompt names a temporary `results/images` directory. A
backend places only final PNG, JPEG, GIF, or WebP files there; after a successful
run, the Slack bridge validates and uploads them to the originating thread. The
directory is deleted when that invocation finishes.
