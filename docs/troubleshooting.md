# Troubleshooting

Start with `./tag doctor`, then use the first failed check below.

| Failure | What it means | Fix |
| --- | --- | --- |
| `mfs-server` or `mfs` missing/wrong version | The pinned memory runtime is unavailable | Run `./install.sh` again and ensure uv's tool directory and `~/.local/bin` are on `PATH`. |
| MFS health fails | Nothing is listening at `MFS_URL` | Run `./tag start`; inspect `./tag logs` and `~/.mfs/server.log`. |
| MFS status has no connectors | MFS has no indexed source | Add a source with MFS, then include its exact root in `MFS_ALLOWED_SCOPES`. |
| MFS scope fails | The scope is absent, outside policy, or its connector credential is unavailable | Compare the exact URI with `mfs ls`; restart MFS after exporting credentials referenced by connector configuration. |
| Slack app token fails | Socket Mode cannot connect | Create an `xapp-` app-level token with `connections:write`. |
| Slack bot token fails | Web API calls cannot authenticate | Reinstall the Slack app and copy its `xoxb-` bot token into the private `.env`. |
| Slack channel/history fails | The bot is absent or lacks scopes | Invite the bot, use the channel ID, and reinstall after changing manifest scopes. |
| Codex missing | The supported backend is not available | Install/login to Codex CLI and confirm `codex --version` works in the same shell. |
| Bridge immediately stops | Runtime dependency or configuration failed after preflight | Run `./tag logs`; rerun `./scripts/ci_check.sh` before reporting a bug. |
| Mention receives no reply | Slack did not emit an event or the bridge rejected the channel | Confirm Socket Mode is connected, mention `@OpenMax` from a human account, and verify `SLACK_CHANNEL_ID`. |

When reporting a problem, include the Tag version, operating system, Python
version, failing check, and redacted log excerpt. Never include tokens.
