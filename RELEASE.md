# Release contract

## v0.1.0-alpha

The supported alpha path is **Slack + Codex CLI + a local MFS server** on macOS
or Linux. It is intended for trusted, isolated sandbox use.

Claude Code and Zulip integrations are included for experimentation, but they
are not part of the v0.1.0-alpha launch qualification unless their live checks
are recorded separately. Windows, hosted operation, enterprise policy,
automated Slack OAuth, and production-grade sandboxing are out of scope.

The canonical source repository is <https://github.com/klovr-co/tag>. Release
tags use the `v<version>` form, so the version in `VERSION` corresponds to the
Git tag `v0.1.0-alpha`. Alpha releases must be published as GitHub prereleases.

Before publishing, the release owner must verify the repository's automated
gate, clean-checkout installation smoke tests, and live Slack sandbox evidence.
Changing repository visibility remains an explicit owner action.
