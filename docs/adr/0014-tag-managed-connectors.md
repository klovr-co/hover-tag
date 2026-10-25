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

Both backends key an MCP sign-in by server name and URL, but each keeps its own
store and registers as its own OAuth client, so a Tag installation that uses
both would need two sign-ins per connector. Tags also run Claude and Codex side
by side, and many providers rotate refresh tokens on every use: two processes
refreshing the same sign-in at once can make the provider revoke it.

[mcp-remote](https://github.com/geelen/mcp-remote) (MIT, about 560,000 npm
downloads a week in October 2026) is a local stdio bridge to a remote MCP
server. It signs in once, keeps the tokens in a folder it is given
(`MCP_REMOTE_CONFIG_DIR`), refreshes them under a machine-wide lease so only
one process uses a refresh token at a time, and supports separate sign-ins per
account (`--resource`) and device-code sign-in for machines without a browser.
Every client that starts it with the same folder shares the sign-in.

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
- **One sign-in per connector, kept in Tag's folder by mcp-remote.** Tag
  installs one pinned mcp-remote version into its installation folder (a
  lockfile with checksums, installed with `npm ci --ignore-scripts`; never
  `npx` at run time) and gives each connector its own folder under
  `<installation root>/state/connector-auth/<name>`. mcp-remote keys sign-ins
  by URL alone, so the folder keeps two accounts on one service apart.
  Tag's own code doesn't read or write tokens; it reads only whether a sign-in
  file exists and when it expires. ADR 0001's trusted-sandbox model covers
  these files like any other credential the backend can reach.
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
  A remote connector is passed to both as the same local command, mcp-remote
  with the connector's URL, so both backends use the one sign-in. A remote
  connector that isn't signed in yet is left out of the run rather than
  starting a sign-in in the middle of a request. The workspace's own
  `.mcp.json` / `.codex/config.toml` servers win on a name clash. A connector a
  Tag may not use is never defined in its runs, and the operator's own sessions
  don't see Tag's connectors.
- **Environment variable names, never values.** Claude gets them as
  `env: {"NAME": "${NAME}"}`, which Claude expands when it starts the server;
  Codex gets `env_vars = ["NAME"]`.
- **Sign-in and status.** `tag connector sign-in NAME` runs mcp-remote's
  client once (browser, or `--device-code` where the provider supports it).
  Status has three levels: the sign-in file (instant: not signed in, expired,
  signed in); a live connection that lists the server's tools (on request, in
  Tag.app and in `tag doctor`); and, during runs, the backends' report of
  connectors that failed to start, which Tag turns into a note in the Slack
  thread and Tag.app. Anything unrecognised is `unknown`, never connected. A
  server that connects without asking anyone to sign in is recorded as needing
  none. Removing a connector deletes its sign-in folder.
  `tag doctor` checks Node and the pinned mcp-remote version.
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
- **No gateway now.** mcp-remote is a per-run command, not a service: no
  process stays running and no port is open, and the sign-in is usable only
  inside the run that started it. ToolHive (Stacklok) and the Docker MCP
  Toolkit were considered; both run a long-lived local proxy or gateway, which
  Tag would have to supervise and protect, and Docker's needs Docker Desktop.
  Revisit for per-person connectors (#105), many machines or a hosted Tag
  sharing one set of sign-ins, or central audit and rate limits; ToolHive is
  the first candidate then. Several fixed accounts for one service need no
  gateway: add the service twice under different names (`notion-acme`,
  `notion-client`), each with its own sign-in.

## Consequences

- One place to add a connector and one sign-in per account, shared by both
  backends and every allowed Tag.
- A connector exists only in the runs of Tags allowed to use it; nothing is
  written to the backends' user-level config, and the operator's own Claude
  Code and Codex sessions are unaffected.
- Remote connectors need Node. `tag doctor` reports when it is missing; local
  (`--command`) connectors don't need it.
- Tag depends on mcp-remote, maintained mostly by one person. The version is
  pinned and updated deliberately; its MIT licence allows a fork if it stops
  being maintained. An older version had a command-injection flaw
  (CVE-2025-6514), which is why it is never fetched at run time.
- Sign-in files are readable by the account Tag runs as, so an agent with shell
  access can read them, as ADR 0001 already states for other credentials.
- Status from the sign-in file follows mcp-remote's file layout; a test pins it,
  and an unreadable layout shows `unknown`.
