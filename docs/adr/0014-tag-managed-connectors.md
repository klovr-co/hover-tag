# ADR 0014: Tag manages one list of MCP connectors, with no gateway

- Status: Proposed
- Date: 2026-10-25
- Related: [ADR 0001](0001-credential-boundary.md),
  [ADR 0004](0004-codex-app-server.md), [ADR 0005](0005-multiple-tag-instances.md),
  [ADR 0007](0007-self-contained-tag-folders.md),
  [ADR 0008](0008-claude-agent-sdk.md), ADR 0012 (Tags as personas, PR #238),
  issue #260, #33, #105, #176, #208

## Context

Integrations (MCP connectors) are added by hand, per Tag and per backend:
Claude reads the workspace `.mcp.json` and Codex reads the workspace
`.codex/config.toml` `[mcp_servers.*]`. Running several Tags, or switching a
Tag between Claude and Codex, means adding the same connector several times,
and nobody can see in one place which Tag can use what.

Both backends already load user-level MCP servers on every run, not only the
workspace ones: Claude runs use the `user` setting source (`~/.claude.json`
`mcpServers`, or `$CLAUDE_CONFIG_DIR/.claude.json`), and Codex reads
`$CODEX_HOME/config.toml`. Writing Tag's connectors there would give every Tag
every connector, and the operator's own Claude Code and Codex sessions too.

Both backends key an MCP sign-in by server name and URL only
(`name|sha256({"type":"http","url":…,"headers":{}})`), not by where the server
is defined. A server defined for one run therefore reuses a sign-in made
elsewhere with the same name and URL. The two backends keep separate stores and
register as separate OAuth clients, so a sign-in in one doesn't reach the other.

MCP gateways (agentgateway, IBM ContextForge, Archestra, MCPHub, Docker MCP
Gateway) offer one endpoint, central sign-in, access rules and audit logs, but
they are a service to run and secure, built for organisations with many tools
across teams.

## Decision

- **One installation-level list.** Tag keeps connectors in
  `<installation root>/state/connectors.json`, shared by all Tags like ChatGPT
  accounts. An entry holds a unique name, transport (`http` or `stdio`), URL or
  command and arguments, environment variable *names* (never values), and the
  allow list. Writes take the installation lifecycle lock and replace the file
  atomically.
- **Tag stores no tokens.** Sign-ins stay in each backend's own store
  (Claude's credential store, Codex's MCP OAuth store). Tag records neither
  tokens nor sign-in state; it reads status from the backend when asked. This
  keeps ADR 0001's credential boundary.
- **Allow list, default none.** Each connector lists the Tags (and, with ADR
  0012, the personas within a Tag) that may use it. A new connector is allowed
  for no Tag, so adding one never silently widens access. Tag.app pre-ticks the
  only Tag on a single-Tag installation; the choice stays visible and explicit.
  Renaming or removing a Tag updates the list.
- **Given per run, never written into the backends' config.** Each run is
  given only the connectors its Tag (or persona) may use:
  - Claude: the SDK's `mcp_servers` gets the workspace's `.mcp.json` servers
    plus the allowed connectors.
  - Codex: one `-c mcp_servers.<name>={…}` override per allowed connector, as
    the workspace's own servers already are.
  The workspace's own `.mcp.json` / `.codex/config.toml` servers win on a name
  clash. A connector a Tag may not use is never defined in its runs, and the
  operator's own sessions don't see Tag's connectors.
- **Environment variable names, never values.** Claude gets them as
  `env: {"NAME": "${NAME}"}`, which Claude expands when it starts the server;
  Codex gets `env_vars = ["NAME"]`.
- **Sign-in, once per backend.** Because sign-ins are keyed by name and URL,
  `tag connector sign-in NAME` signs in once per backend and every allowed Tag
  reuses it. For Claude, Tag keeps a local-scope server entry in a Tag-owned
  folder (`claude mcp add --scope local`, visible only there) and runs
  `claude mcp login NAME` from it; for Codex it runs
  `codex mcp login NAME -c mcp_servers.NAME={url=…}` and writes nothing. Tag
  relays the authorization URL, as `tag settings ai sign-in` does for the
  backends themselves, and asks only for the backends the allowed Tags use. Status comes from `codex mcp list --json` (`auth_status`) and
  from `claude mcp get NAME`; anything unrecognised is reported as `unknown`,
  never as signed in.
- **Surfaces.** `tag connector add|list|show|remove|allow|deny|import|sign-in|search`
  with `--json`; connector checks in `tag doctor`; a Connectors section in
  Tag.app Settings, and each Tag's allowed connectors in its Details.
- **Online store.** Tag lists connectors from the official MCP Registry
  (`registry.modelcontextprotocol.io`) rather than a list shipped with Tag:
  `tag connector search TEXT` and a Browse view in Tag.app. Picking one fills
  in `add` with the registry's remote URL; the allow-list default still
  applies. Only remote (HTTP) entries can be added from the store at first;
  entries that run a local command stay a manual `add --command`, because they
  would run unreviewed code under the user's account. Results show the
  publisher's namespace (for example `app.linear/linear`).
- **Migration.** `tag connector import` offers the servers already in a Tag's
  workspace `.mcp.json` and `.codex/config.toml`, allowed for that Tag only,
  and removes them from the workspace file after backing it up.
- **No gateway now.** Revisit only for per-person connectors (#105), many
  machines or a hosted Tag sharing one set of sign-ins, or central audit and
  rate limits a customer requires; then start with something light. Several
  fixed accounts for one service need no gateway: add the service twice under
  different names (`notion-acme`, `notion-client`), which gives each its own
  sign-in. Acting as each requester's own account (#105) does need one, so that
  Tag can hold per-person sign-ins without handing tokens to the agent.

## Consequences

- One place to add a connector, and one sign-in per backend (at most two) that
  every allowed Tag reuses.
- A connector exists only in the runs of Tags allowed to use it; nothing is
  written to the backends' user-level config, and the operator's own Claude
  Code and Codex sessions are unaffected.
- Sign-in needs a definition the backend CLI can see; for Claude that is a
  local-scope entry under a Tag-owned folder, which Tag adds and removes with
  the connector.
- Claude has no machine-readable MCP status, so its sign-in status is parsed
  from CLI text and may read `unknown` after a Claude update.
- If a backend changes how it keys MCP sign-ins, runs would ask for sign-in
  again; `tag doctor` reports it.
