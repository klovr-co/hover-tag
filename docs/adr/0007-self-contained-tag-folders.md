# ADR 0007: Keep each Tag's data in its working folder

- Status: Accepted
- Date: 2026-09-26
- Supersedes: ADR 0006's separation of per-Tag application data and working files;
  ADR 0005's native per-instance storage paths.

## Context

Copying the visible Tag folder before a Mac reset preserves working files but
misses configuration, credentials, and conversation history in Application
Support. An operator should be able to locate all data belonging to one Tag in
one folder without learning its installation layout.

## Decision

For normal installations on every platform, `~/Tag/NAME` remains the working
folder and `~/Tag/NAME/.tag` becomes the private instance home. The default Tag
uses `~/Tag/default`. The private home contains metadata, configuration,
credentials, integrations, state, and temporary files. Installed releases,
launchers, global backend authentication, and the shared MFS service/index
remain installation-wide. An explicit non-standard `TAG_HOME` retains the
existing self-contained portable/development layout.

Discovery reads the `.tag/instance.json` metadata and deduplicates legacy
instances by alias. Read-only commands can inspect legacy homes without moving
them. Startup first runs older layout migrations, then stops the affected
bridge and copies its private home. It rewrites managed JSON paths and TOML
connector credential references, preserves unrelated settings and original
files, verifies the copy, and commits a versioned checkpoint last. Before that
checkpoint, the old home remains authoritative. Conflicts or interruptions do
not commit partial migrations. Startup resolves the context again and loads
credentials from the resulting home before dependent services and checks.

Private directories use owner-only access and `.tag/.gitignore` excludes their
contents. These controls do not strengthen ADR 0001's trusted-sandbox model:
the coding agent has the user's permissions. Cloud syncing a Tag folder also
syncs its credentials. Moving a Tag does not transfer global backend sign-ins
or the shared memory index.

## Consequences

- Working files and per-Tag state have one visible parent folder.
- Existing installations migrate automatically without repeating setup.
- Original legacy data is retained for recovery but is no longer authoritative.
- Runtime process identity remains validated; copied PID records do not prove
  that a process belongs to Tag.
- Rollback to a release that cannot understand this layout must be refused.

## Channel artifact organization

New saved deliverables default to `artifacts/CHANNEL_ID` inside the Tag's working
folder. Stable Slack conversation IDs avoid name collisions and keep channel
renames from moving files. The backend still runs from the Tag workspace so its
existing skills, configuration, repository work, and legacy files remain
available. Explicit output paths and in-place edits take precedence.

Each invocation prepares its channel folder idempotently before running the
backend. Existing files have no reliable channel provenance, so they stay in
place rather than being assigned to a guessed channel during upgrade. Relative
artifact registration uses the channel folder; manifests and local Open actions
continue using validated workspace paths. Temporary results remain isolated by
invocation and are cleaned up normally. This organization is not a new access
boundary between channels.
