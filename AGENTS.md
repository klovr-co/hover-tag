# Repository instructions

## Agent skills

### Issue tracker

Issues and PRDs are tracked in GitHub Issues for `klovr-co/tag`. Feature planning
uses the Tag Features catalog: https://github.com/orgs/klovr-co/projects/3.
Follow its existing feature naming, draft lifecycle, and field conventions.
See `docs/agents/issue-tracker.md` for commands and the authentication fallback
when the integration token cannot access Projects.

### Feature catalog

Proposed product features belong in the
[`Tag Features` GitHub Project](https://github.com/orgs/klovr-co/projects/3/views/1)
and must also be tracked as issues in `klovr-co/tag`. Create the issue, add it to
the project with Status `Idea`, give it the appropriate Area and Tier, set
Verification to `Missing`, and set Version to `TBD`. Keep the issue as the
canonical record for discussion, specification, and implementation history.

### Triage labels

Use the repository's default five-role triage vocabulary. See
`docs/agents/triage-labels.md`.

### Pull request release behavior

Every eligible PR merged into `main` automatically publishes an immutable,
numbered alpha release after CI and clean-install checks pass. Before merging,
use at most one of these release labels:

- No release label — continue the active alpha line. For example,
  `v0.2.0-alpha.3` becomes `v0.2.0-alpha.4`. If no numbered alpha line is
  active, automation starts the release line configured by `VERSION`.
- `release:next-patch` — start the next patch line at `alpha.1`. For example,
  `v0.1.0-alpha` becomes `v0.1.1-alpha.1`.
- `release:next-minor` — start the next minor line at `alpha.1` and reset the
  patch component. For example, `v0.1.1-alpha.2` becomes `v0.2.0-alpha.1`.
- `release:skip` — merge without publishing an immutable alpha release. The
  moving `edge` build may still update.

Do not add `release:next-minor` for ordinary alpha increments. Conflicting or
unknown `release:*` labels intentionally prevent publication. Beta and stable
releases remain manually qualified and published; see `RELEASE.md`.

### Domain docs

This is a single-context repository. Read the root `CONTEXT.md` and relevant
ADRs under `docs/adr/` when they exist. See `docs/agents/domain.md`.
