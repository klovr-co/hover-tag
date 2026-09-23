# ADR 0005: Resolve independent Tag instances over one shared installation

- Status: Accepted
- Date: 2026-09-21

## Context

Operators need to connect separate Tag assistants to different Slack
workspaces without duplicating the installed runtime. Historically `TAG_HOME`
identified both the installed application and the only mutable configuration.
The MFS process record also lived beside that configuration, so stopping or
resetting the only Tag stopped memory as a side effect.

In this document, a **Slack workspace** is Slack's Team-ID-scoped service. An
**agent working folder** is the local directory in which a backend executes.
A **Tag instance** is one local Slack bridge, configuration, identity, working
folder, conversation state, and lifecycle. These terms are not interchangeable.

## Decision

`TAG_HOME` continues to mean the installation root. Every instance, including
the built-in `default`, owns its mutable data below `instances/NAME/`. A
validated lowercase local name resolves to that directory; child processes
receive the path explicitly through
`TAG_INSTANCE_HOME` and receive the stable local identifier through `TAG_ID`.
Instance-scoped Slack, MFS, and OpenTag variables are scrubbed before the saved
settings for the selected instance are loaded; `default` has no environment
inheritance exception. The two startup-attempt tuning variables are explicit
operational overrides: `OPENTAG_MFS_STARTUP_ATTEMPTS` and
`OPENTAG_STARTUP_ATTEMPTS` are carried across the scrub and take precedence
over saved extension keys.

Named instances are created through a staging directory and an atomic rename.
Their versioned, non-secret `instance.json` is the discovery authority. Invalid
or incomplete entries are reported independently so they cannot hide healthy
peers. Symlinks, traversal, ambiguous casing, and the reserved name `default`
are rejected.

Releases, launchers, backend account authentication, and supported global
backend configuration remain installation-wide. Each instance owns its saved
settings, Slack app identity, workspace, MCP overlay, temporary files, logs,
heartbeats, PID record, settings drafts, and active-session state. This does
not strengthen the trusted-sandbox credential boundary accepted in ADR 0001.

One locally managed MFS service is shared. Its process identity, startup lock,
and logs live under `shared/mfs/`. Instance start may ensure that service is
healthy, but instance stop, restart, reset, failed bridge startup, and
development cleanup never stop that shared process. Reset also unregisters the
selected instance's connector and removes the records owned by that connector;
it does not affect other connectors. Explicit
`tag memory stop` refuses while any bridge is running and refuses to claim an
externally managed process. A legacy default-instance process record is moved
only after its PID, creation time, and command identity are verified.

Each local Slack connector reads its history token from an owner-only,
per-instance credential file. New connector roots include both stable Slack
Team ID and App ID. Existing roots are retained during credential migration so
an upgrade does not cause destructive reindexing. A local `file:` reference is
never presented as valid for a remote MFS endpoint.

Normal retrieval remains limited to the selected instance's approved Slack
scopes. Sharing an MFS database is storage reuse, not cross-workspace
authorization. Cross-workspace search and identity mapping require a future
decision and explicit operator authorization.

## Consequences

- Unqualified commands select `instances/default`; installations using the old
  root-level mutable layout are migrated automatically on startup after stopping
  the affected bridge. Settings, integrations, and instance state are copied
  with originals preserved and managed paths updated for the new home.
- Upgrade and rollback stay installation-wide; rollback is blocked while any
  managed bridge or shared MFS process is running.
- Separate Slack apps may use different names and profile images, but remote
  appearance changes remain an operator action in each app's Slack settings.
- Old and new CLI releases must not issue lifecycle commands concurrently
  during the shared-MFS ownership migration.
- Multiple local instances are operational isolation, not a hardened tenant
  boundary against a shell-capable backend or same-account process.
