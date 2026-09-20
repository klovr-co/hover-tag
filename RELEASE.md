# Release contract

## v0.1.1-alpha

The supported alpha path is **Slack + Codex CLI + a local MFS server** on macOS
or Linux. It is intended for trusted, isolated sandbox use. Native Windows
installation and lifecycle support are included in the CI matrix; live Windows
Slack/backend qualification must be recorded before claiming that path qualified.

Claude Code is included for experimentation, but is not part of the
v0.1.1-alpha launch qualification unless its live checks are recorded
separately. Hosted operation, enterprise policy, automated Slack OAuth,
and production-grade sandboxing are out of scope.

The canonical source repository is <https://github.com/klovr-co/tag>. Release
tags use the `v<version>` form, so the version in `VERSION` corresponds to the
Git tag `v0.1.1-alpha`. Alpha releases must be published as GitHub prereleases.

Before publishing, the release owner must verify the repository's automated
gate, clean-checkout installation smoke tests, and live Slack sandbox evidence.
Publishing a GitHub prerelease remains an explicit owner action.

Published releases trigger `.github/workflows/release-package.yml`, which
verifies or creates `tag-<version>.zip`, `SHA256SUMS`, and
`BUILD-PROVENANCE.json`. Download installers require those assets;
the bootstrap endpoints must not be advertised as available before merging and
publishing them. The active installation is independent of the checkout; see
[installation](docs/installation.md).

## Development and promotion workflow

`main` is the only permanent development branch. After both CI and clean-install
smoke tests pass for its current commit, `.github/workflows/edge-build.yml`
publishes a moving `edge` prerelease. The `edge` tag and its stable-named assets
are intentionally replaceable and are not SemVer releases. `BUILD-PROVENANCE.json`
records the full commit SHA, build time, source ref, base version, and archive
digest. A commit-specific copy is retained as a GitHub Actions artifact for 90
days, which is the repository's maximum configured retention period.
Testers can always retrieve the current build from
`https://github.com/klovr-co/tag/releases/download/edge/tag-edge.zip` and should
verify it with the adjacent checksum and provenance assets.

Official release tags remain immutable. A maintainer prepares one by running the
`Prepare release` workflow with the full tested `main` commit SHA and the phase
encoded in that commit's `VERSION`. The workflow requires successful CI and
install-smoke runs for that exact SHA, validates the allowed version transition,
reruns the release-candidate preflight, and promotes the retained archive bytes
into a draft GitHub release. Draft preparation does not require publication
approval. The workflow never publishes the draft: a maintainer must inspect it
and publish it explicitly; alpha and beta drafts must be marked as prereleases.

Live validation evidence names the candidate commit that was exercised. Because
a commit cannot contain its own SHA, the release commit may follow that candidate
only to record its evidence file; the preflight rejects changes to every other
path between the named candidate and the promoted commit. CI, install smoke, and
the retained edge artifact are still required for the exact promoted SHA.

The version is selected in source before the candidate commit is tested. Normal
work after `vX.Y.Z` starts `vX.(Y+1).0-alpha.1`; urgent maintenance may instead
start `vX.Y.(Z+1)-alpha.1`. Candidates advance without skipping counters from
alpha to beta to stable. The existing unnumbered `v0.1.x-alpha` line remains
supported as a legacy transition, but new lines use numbered candidates.

Publishing a prepared draft triggers `.github/workflows/release-package.yml`.
For promoted builds it verifies the attached archive, checksum, provenance,
internal version, and prerelease setting instead of rebuilding. Older releases
without prepared assets retain the original build-on-publication fallback.
