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
`$CODEX_HOME/config.toml`. All Tags on a machine run under one user account, so
a user-level server and its sign-in already reach every Tag.

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
- **Written into both backends' user-level config through their own CLIs.**
  `claude mcp add-json --scope user` / `claude mcp remove --scope user` and
  `codex mcp add` / `codex mcp remove`, so each backend keeps owning its file
  format and locking. A backend whose CLI is missing is marked pending and
  synced later (`tag connector sync`, `tag doctor`).
- **Ownership.** Tag records, per backend, the definition it wrote. It updates
  or removes an entry only while the backend's entry still matches that record;
  an entry changed outside Tag is reported and left alone. `add` refuses a name
  that already exists in a backend and isn't Tag's, and suggests
  `tag connector import`.
- **Allow list, default none.** Each connector lists the Tags (and, with ADR
  0012, the personas within a Tag) that may use it. A new connector is allowed
  for no Tag, so adding one never silently widens access. Tag.app pre-ticks the
  only Tag on a single-Tag installation; the choice stays visible and explicit.
  Renaming or removing a Tag updates the list.
- **Per-run filtering by disabling, not by adding.** Because both backends
  already load user-level servers, each run turns off the Tag-managed
  connectors its Tag (or persona) may not use:
  - Codex: the thread's `config` override sets
    `mcp_servers.<name>.enabled = false`, as text-only summaries already do.
  - Claude: the per-run `settings` add `permissions.deny: ["mcp__<name>"]`,
    which removes the server's tools from the run.
  Servers Tag doesn't manage, and the workspace's own `.mcp.json` /
  `.codex/config.toml` servers, behave as before.
- **Environment variable names, never values.** Claude gets them as
  `env: {"NAME": "${NAME}"}`, which Claude expands when it starts the server.
  `codex mcp add` can only set values, so Tag writes the Codex server without
  them and each run passes `mcp_servers.<name>.env_vars = ["NAME"]` for the
  connectors the Tag may use. The operator's own Codex sessions don't get them.
- **Sign-in.** `tag connector sign-in NAME --backend claude|codex` runs
  `claude mcp login NAME` or `codex mcp login NAME` and relays the
  authorization URL, as `tag settings ai sign-in` does for the backends
  themselves. Status comes from `codex mcp list --json` (`auth_status`) and
  from `claude mcp get NAME`; anything unrecognised is reported as `unknown`,
  never as signed in.
- **Surfaces.** `tag connector add|list|show|remove|allow|deny|sync|import|sign-in`
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
  rate limits a customer requires; then start with something light.

## Consequences

- One place to add a connector and sign in at most once per backend; both
  backends get the same list.
- Connectors written to user-level config also appear in the operator's own
  Claude Code and Codex sessions on that machine. That's the price of sharing
  each backend's sign-in; `tag connector list` and the docs say so.
- Per-Tag isolation is only as strong as the backends' disable mechanisms. A
  disabled Claude server may still start; only its tools are denied. Tags on
  one machine already share the account, so this restricts what a Tag offers
  its requesters, not what the machine can reach.
- Claude has no machine-readable MCP status, so its sign-in status is parsed
  from CLI text and may read `unknown` after a Claude update.
- Tag now edits files it doesn't own, though only through their CLIs and only
  entries it created.
