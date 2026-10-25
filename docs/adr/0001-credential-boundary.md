# ADR 0001: Treat backend credentials as trusted-sandbox capabilities

- Status: Accepted
- Date: 2026-09-18

## Context

The Slack bridge launches a local coding-agent backend. It removes the Socket
Mode app token and Slack access-control configuration, but the backend retains
the bot token, MFS token, and other credentials in the bridge environment. A
backend with shell access can therefore bypass Tag's scoped helper commands.

## Decision

The alpha uses a trusted-sandbox model rather than a credential broker. MFS and
Slack helper restrictions are application guardrails, not hardened capability
boundaries. Operators must isolate the host or local account and supply
least-privilege credentials.

Security and setup documentation must state this inheritance directly. Claims
of credential isolation must specify that only the Socket Mode token and bridge
access-control settings are withheld from backend processes.

## Consequences

- Tag remains unsuitable as a production security boundary.
- A compromised or prompt-injected backend can exercise inherited credential
  permissions directly.
- A future hardened deployment requires a broker that owns credentials and
  exposes only authenticated, scoped operations; environment filtering alone
  is insufficient.

## Amendment, 2026-10-26: connector sign-ins

[ADR 0014](0014-tag-managed-connectors.md) adds MCP connector sign-ins to the
credentials on the host. They are OAuth tokens that mcp-remote keeps under
`<installation root>/state/connector-auth`, readable only by the account Tag
runs as. The trusted-sandbox model applies to them unchanged: a backend with
shell access can read them, so operators grant connectors the least access the
Tags need, and allow each connector only for the Tags that use it.
