# Adding integrations

Integrations let Tag use services such as Gmail from Slack. You make them
available through the agent running Tag's tasks. This guide covers Codex;
Claude remains experimental.

## Commands, skills, and MCP

Codex can use installed commands, skills, and configured MCP connections.
They serve different purposes:

| Item | What it provides | Example |
| --- | --- | --- |
| Command | A program the agent can run | `gws` makes Google Workspace API requests |
| Skill | Instructions for carrying out a task | A Gmail skill explains how to find and read mail |
| MCP connection | Tools exposed by a configured server | A server can provide document-search tools |

## Configure integrations

Tag keeps your global Codex configuration and login available, subject to
Codex's own loading rules. If a skill or connection already works globally,
you may not need another copy. Put additions specific to Tag in these locations,
relative to the [workspace](workspaces-and-tools.md#find-your-workspace):

| Configuration | Location |
| --- | --- |
| Codex skills | `.agents/skills/<skill-name>/SKILL.md` |
| Codex MCP servers | `.codex/config.toml`, under `[mcp_servers.NAME]` |

Tag explicitly passes the workspace's MCP server definitions to Codex. It does
not apply other settings from that file through this mechanism. For MCP
examples and the experimental Claude paths, see
[installation and integrations](../installation.md#integrations).

## Make commands and logins available

Commands must be available in the environment that starts Tag. The Tag home's
`integrations/bin` directory is also on its command search path. Tool logins
remain in their normal locations; adding a skill does not install a command or
authenticate it. The backend inherits credentials available in its process
environment, as described in the [security policy](../../SECURITY.md).

Try it: [Use Gmail from Slack](../tutorials/use-gmail-from-slack.md).
