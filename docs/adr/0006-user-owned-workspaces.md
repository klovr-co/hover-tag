# ADR 0006: Separate user-owned workspaces from application data

- Status: Accepted
- Date: 2026-09-22

## Context

ADR 0005 placed every instance's configuration, working folder, integrations,
state, and temporary files below the platform application-data directory. That
layout isolates instances, but it also hides the agent working folder even
though operators are expected to browse, edit, back up, and open its contents
in development tools.

The working folder and runtime state have different owners. Treating them as a
single directory makes it too easy to sync credentials and process records with
ordinary project files, or to overlook the working folder entirely.

## Decision

For a normal platform installation, each Tag's user-owned working folder lives
at `~/Tag/NAME`; the built-in Tag therefore uses `~/Tag/default`. Configuration,
credentials, integrations, conversation and process state, temporary files,
releases, and shared MFS state remain in the platform application-data home.

The resolved working folder is exposed through the instance context and passed
to child processes as `OPENTAG_WORKDIR`. Callers do not derive it from the
instance home. An explicit non-standard `TAG_HOME` keeps its workspace below
`instances/NAME/workspace`, preserving the existing self-contained behavior for
development, tests, and portable installations.

Startup migrates old working folders automatically, including the root-level
default workspace and per-instance app-data workspaces. The affected bridge is
stopped first. Original files are retained, existing destination files are never
silently replaced, and a versioned checkpoint is written only after a complete
copy. Interrupted copies can be retried. Conflicts preserve both versions and
identify the destination requiring operator resolution.

This decision supersedes ADR 0005 only where that ADR says the agent working
folder lives below `instances/NAME`. The instance remains the owner of the
working folder and all authorization and lifecycle decisions remain unchanged.

## Consequences

- User-editable files are visible in an unsurprising home-directory location.
- App-managed credentials, logs, process records, and temporary files remain
  separate from files users may sync or commit.
- `tag paths` is the authority for both instance and workspace locations.
- Backups that need a complete Tag must include both the application-data home
  and `~/Tag`.
