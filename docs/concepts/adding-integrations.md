# Adding integrations

Integrations let Tag use services such as Gmail from Slack. You make them
available through the agent running Tag's tasks. Each Tag uses Codex or
Claude, chosen with its AI model; if you switch a Tag to the other backend, or
run Tags with both, add the integration for each.

## Commands, skills, and MCP

Both agents can use installed commands, skills, and configured MCP connections.
They serve different purposes:

| Item | What it provides | Example |
| --- | --- | --- |
| Command | A program the agent can run | `gws` makes Google Workspace API requests |
| Skill | Instructions for using tools, sometimes with bundled scripts | A Gmail skill explains how to find and read mail using `gws` |
| MCP connection | Tools exposed by a configured server | A server can provide document-search tools |

## Configure integrations

If a skill or MCP connection already works globally in Codex or Claude Code,
you may be able to use it through Tag without configuring it again. Tag keeps
your global agent configuration and login available when it runs under the same
local account and environment, subject to each agent's own loading rules.

For additions specific to Tag, [find your workspace](workspaces-and-tools.md#find-your-workspace)
and use these locations relative to that folder:

| Configuration | Location |
| --- | --- |
| Codex skills | `.agents/skills/<skill-name>/SKILL.md` |
| Codex MCP servers | `.codex/config.toml`, under `[mcp_servers.NAME]` |
| Claude skills | `.claude/skills/<skill-name>/SKILL.md` |
| Claude MCP servers | `.mcp.json`, under `mcpServers` |

When running in the installed Tag home's workspace, Tag explicitly passes
the MCP server definitions in `.codex/config.toml` to Codex. This mechanism
does not forward other settings from that file. Claude runs likewise receive
the servers in the workspace `.mcp.json`.

Learn more about configuring and authenticating MCP connections in the
[Codex MCP guide](https://developers.openai.com/codex/mcp) and the
[Claude Code MCP guide](https://docs.anthropic.com/en/docs/claude-code/mcp).

## Use your signed-in Chrome

Claude Tags can use Claude in Chrome, which acts in the Chrome on the computer
running Tag, with its sign-ins. It is off by default. To turn it on, open the
Tag in Tag.app, go to **Details → Permissions**, set **Chrome** to **On**, and
click **Save and restart**. From the terminal, run:

```sh
tag NAME settings ai permissions claude-chrome=1 --restart
```

Keep Chrome open with the Claude in Chrome extension signed in while the Tag
works. Browser actions ask for approval in Slack unless the Tag's permissions
allow them. Anyone who may ask the Tag can then ask it to act on sites where
that browser is signed in. Codex Tags use a browser MCP server added to
`.codex/config.toml` instead.

## Make commands and logins available

Follow the skill's setup instructions for any required tools. Make sure any
required command works from the terminal you use to start Tag.

If you change the command search path or environment, run `tag restart` from
that terminal so Tag receives the changes. A thread continues its
earlier agent conversation, so try a new integration in a new Slack thread.

Each tool or connector handles its own authentication. Follow its authentication
instructions. If it's already authenticated and that login is available to Tag,
you can reuse it.

The agent also inherits credentials available in Tag's process environment,
such as API keys. See the [security policy](../../SECURITY.md) for details.

## Try an integration

[Use Gmail from Slack](../tutorials/use-gmail-from-slack.md)

Ask Tag to set up Gmail, find an email, and draft a reply from Slack.
