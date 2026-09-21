# Release contract

## v0.2 alpha line

The supported alpha path is **Slack + Codex CLI + a local MFS server** on macOS
or Linux. It is intended for trusted, isolated sandbox use. Native Windows
installation and lifecycle support are included in the CI matrix; live Windows
Slack/backend qualification must be recorded before claiming that path qualified.

Claude Code is included for experimentation, but is not part of the
v0.2 alpha qualification unless its live checks are recorded
separately. Hosted operation, enterprise policy, automated Slack OAuth,
and production-grade sandboxing are out of scope.

The canonical source repository is <https://github.com/klovr-co/hover-tag>. `VERSION`
selects the current release candidate (`v0.2.0-beta.1`). On an alpha source
line, automatic releases append a monotonically increasing candidate number
such as `v0.2.0-alpha.3`. Alpha releases are GitHub prereleases and remain
explicitly experimental.

Published releases trigger `.github/workflows/release-package.yml`, which
verifies or creates `tag-<version>.zip`, `SHA256SUMS`, and
`BUILD-PROVENANCE.json`. Download installers require those assets;
the bootstrap endpoints must not be advertised as available before merging and
publishing them. The active installation is independent of the checkout; see
[installation](docs/installation.md).

## Development and promotion workflow

`main` is the only permanent development branch. After both CI and clean-install
smoke tests pass for a merged commit, `.github/workflows/edge-build.yml`
publishes an immutable numbered alpha or beta by default, according to the
phase selected in `VERSION`. It also updates the moving
`edge` prerelease when that commit is still the head of `main`. The `edge` tag and its stable-named assets
are intentionally replaceable and are not SemVer releases. `BUILD-PROVENANCE.json`
records the full commit SHA, build time, source ref, base version, and archive
digest. A commit-specific copy is retained as a GitHub Actions artifact for 90
days, which is the repository's maximum configured retention period.
Testers can always retrieve the current edge build from
`https://github.com/klovr-co/hover-tag/releases/download/edge/tag-edge.zip` and should
verify it with the adjacent checksum and provenance assets.

Automatic prereleases use the exact bytes retained for their commit-specific
edge artifact. A merged PR needs no release label for the normal path. Apply
`release:skip` to publish no prerelease, `release:next-patch` to start the next
patch line, or `release:next-minor` to start the next minor line. Conflicting release
labels fail closed and publish nothing. Patch and minor labels apply only to
alpha lines. Once a line exists, unlabeled merges advance its alpha or beta
candidate number.

Moving from alpha to beta is a source change: update `VERSION` to the complete
semantic beta version `<major>.<minor>.<patch>-beta.1` (for example,
`0.2.0-beta.1`) and merge it normally. After the standard CI and clean-install
gates pass, the edge workflow publishes the beta automatically from the
retained artifact. Later eligible merges on that source line publish `beta.2`,
`beta.3`, and so on. Betas do not require a live Slack probe, evidence-only
follow-up, draft, or separate publication approval.

Stable releases remain explicit owner actions. A maintainer prepares one by
updating `VERSION`, completing live evidence, and running the `Prepare stable
release` workflow with the full tested `main` commit SHA. The workflow requires
successful CI and install-smoke runs for that exact SHA, validates the version
transition, reruns the release-candidate preflight, and creates a draft from the
retained archive. A maintainer must inspect and publish that draft explicitly.

Live validation evidence for stable names the candidate commit that was
exercised. Because a commit cannot contain its own SHA, the release commit may follow that candidate
only to record its evidence file; the preflight rejects changes to every other
path between the named candidate and the promoted commit. CI, install smoke, and
the retained edge artifact are still required for the exact promoted SHA.

The prerelease line and phase are selected in source; alpha lines may be
advanced explicitly by PR label. Candidate numbers are derived from immutable
published tags. Stable versions are selected in source before their candidate
commit is tested. The existing unnumbered `v0.1.x-alpha` releases remain
supported as a legacy format, but new lines use numbered candidates.

The automatic workflow downloads and verifies the published prerelease assets
after upload. Manually published releases trigger `.github/workflows/release-package.yml`,
which verifies attached archives, checksums, provenance, internal versions, and
the prerelease setting instead of rebuilding. Older releases without prepared
assets retain the original build-on-publication fallback.
