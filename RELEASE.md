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

Published releases trigger `.github/workflows/release-package.yml`, which builds
`tag-<version>.zip` and `SHA256SUMS`. Download installers require those assets;
the bootstrap endpoints must not be advertised as available before merging and
publishing them. The active installation is independent of the checkout; see
[installation](docs/installation.md).
