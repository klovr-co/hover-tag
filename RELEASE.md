# Release contract

## v0.2 alpha line

The supported alpha path is **Slack + Codex CLI + a local MFS server** on macOS
or Linux. It is intended for trusted, isolated sandbox use. Native Windows
installation and lifecycle support are included in the CI matrix; live Windows
Slack/backend qualification must be recorded before claiming that path qualified.

Codex and Claude Code are both supported agents. Record live checks for each
agent before claiming a release qualified for it. Hosted operation, enterprise policy, automated Slack OAuth,
and production-grade sandboxing are out of scope.

The canonical source repository is <https://github.com/klovr-co/hover-tag>. `VERSION`
selects the current release line (`v0.3.1`). The first beta is
`v0.3.0-beta.1`; later eligible merges publish monotonically increasing
candidates such as `v0.3.0-beta.2`. Beta releases are GitHub prereleases
and remain opt-in. The previous line published `v0.2.0-alpha` and
`v0.2.0-beta` candidates before stable `v0.2.0`.

Published releases trigger `.github/workflows/release-package.yml`, which
verifies or creates `tag-<version>.zip`, `SHA256SUMS`, and
`BUILD-PROVENANCE.json`. Download installers require those assets;
the bootstrap endpoints must not be advertised as available before merging and
publishing them. The active installation is independent of the checkout; see
[installation](docs/installation.md).

The Hover site defaults to the newest published stable guide at `/tag/`. The
existing v0.2.0 release is served at `/tag/v0.2/`. Markdown from `main` is
published separately as the `Development` guide at `/tag/development/`; changes
under `docs/**` on `main` propose an update to Development without moving the
stable guide. The separate `docs-v0.2.0` tag initially points to the v0.2.0
release commit. If its guide needs a docs-only correction, create a commit from
that release changing only `docs/**`, then tag it `docs-v0.2.0-r1` (and advance
the revision number for later corrections). The site can pin that docs tag to
update the v0.2.0 guide without moving the software release tag or changing its
downloadable assets. Run the site's **Sync Tag docs** workflow with that docs
tag to propose a reviewable correction PR.
After an immutable numbered alpha, beta, or stable release and its assets are
verified, the release workflows notify the site with the exact tag and commit.
The site validates the release and opens a separate version PR for review.
Merging an alpha or beta PR adds a labeled prerelease guide; only a newer
stable release advances the default. The moving `edge` release does not create
a versioned guide. To retry a notification, run **Notify site docs** with the
published tag. To roll back, revert the site pin commit. See
[issue #132](https://github.com/klovr-co/hover-tag/issues/132).

Public installers resolve channels through `tag-release-channels.json` on the
moving `channels` GitHub release. `.github/workflows/channel-index.yml`
regenerates that index from published, fully attributed releases after edge or
release packaging completes. Release packaging started by the edge build uses
`GITHUB_TOKEN`, which does not fire `workflow_run`, so its final
`channel-index` job dispatches the index directly once Tag.app builds are attached. Once a release has shipped Tag.app, each newer
release also needs `DESKTOP-SHA256SUMS-PLATFORM` for macOS, Windows, and Linux
before its channel moves to it. Until all three builds are attached, the
channel keeps the previous release, so `tag upgrade` never moves past Tag.app's
update feed. The index is a mutable pointer only; numbered
release archives, checksums, and provenance remain immutable. Installers derive
fixed release-asset URLs from the selected tag and retain the GitHub Releases
API only as a compatibility fallback while the index is unavailable.

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
alpha lines and never select a line below `VERSION`. Once a line exists,
unlabeled merges advance its alpha or beta candidate number. While `VERSION`
names a published stable release, unlabeled merges publish no prerelease; set
`VERSION` to the next alpha line, such as `0.3.0-alpha`, or label the PR to
start it.

Each edge build waits for the previous `main` commit to finish its own release
processing so candidate numbers follow merge order. A predecessor that never
ran the required workflows, failed them, or finished its edge build
unsuccessfully does not block later merges; rerun its edge build only if that
commit still needs its own prerelease.

Moving from alpha to beta is a source change: update `VERSION` to the beta
line `<major>.<minor>.<patch>-beta` (for example, `0.2.0-beta`) and merge it
normally. `VERSION` names the line, never a candidate number; the automation
numbers each beta from the published tags. After the standard CI and clean-install
gates pass, the edge workflow publishes the beta automatically from the
retained artifact. Later eligible merges on that source line publish `beta.2`,
`beta.3`, and so on. Betas do not require a live Slack probe, evidence-only
follow-up, draft, or separate publication approval.

Stable releases remain explicit owner actions. A maintainer prepares one by
updating `VERSION`, adding a dated entry with release changes to `CHANGELOG.md`,
completing live evidence, and running the `Prepare stable
release` workflow with the full tested `main` commit SHA. The workflow requires
successful CI and install-smoke runs for that exact SHA, validates the version
transition and changelog entry, reruns the release-candidate preflight, and
creates a draft from the retained archive. A maintainer must inspect and publish
that draft explicitly. Missing or empty stable changelog entries fail CI before
qualification and draft creation.

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

## Release lines

Before merging, decide which release each PR belongs to:

- **The current line** (`VERSION` on `main`): merge it normally.
- **A fix for an older stable release as well:** merge it to `main`, then
  cherry-pick it to the maintenance branch, as described below.
- **A later version:** don't merge it yet. Keep the PR as a draft with a
  `target:vX.Y` label, and merge `main` into it regularly. After the current
  line ships stable, set `VERSION` to the next alpha line and merge the PR.

`main` holds only work for the current line, so the automation never needs a
second development branch.

## Patching an older stable release

`main` carries the next release line. Fixes land on `main` first, and users of
an older stable release get them by upgrading. There is no permanent
maintenance branch.

Create one only when a stable release needs an urgent fix that cannot wait for
the next line. Label the merged PR `backport:vX.Y.x` (or run the **Backport**
workflow with the PR number and branch). The workflow creates `release/vX.Y.x`
from the line's newest stable tag if it doesn't exist, cherry-picks the merged
commit with `-x` onto a `backport/pr-N-to-vX.Y.x` branch, and opens a pull
request against the maintenance branch. On a conflict it comments on the
original PR instead. Set the `BACKPORT_TOKEN` secret so CI runs on that pull
request; without it, close and reopen the pull request to start CI.

Never merge `main` into a maintenance branch, and never land a fix only on the
maintenance branch. Because every fix already exists on `main`, nothing merges
back.

CI and install smoke run on pushes to `release/v*.x`, and the edge build keeps
the exact artifact for each commit without moving `edge` or publishing a
prerelease. A maintenance branch publishes stable patches only, with no alpha
or beta stage. To release one, set `VERSION` to the next patch (for example,
`0.3.1`), add the `CHANGELOG.md` entry and `docs/releases/v0.3.1.md`, merge that
through a pull request, and run **Prepare stable release** with `branch` set to
`release/v0.3.x` and the tested commit SHA. The workflow checks that the commit
is on that branch, that the version is the next stable patch of the same line,
and that the retained artifact came from that branch. A maintainer publishes
the draft. Delete the branch when its line is no longer supported.

## Tag.app

Each published release also builds Tag.app (`desktop/`) on macOS,
Windows, and Linux and attaches `Tag-VERSION-macos.dmg` (universal),
`Tag-VERSION-windows-setup.exe`, `Tag-VERSION-linux-amd64.deb`, and
`Tag-VERSION-linux-x86_64.AppImage`, with checksums in
`DESKTOP-SHA256SUMS-PLATFORM`. These are separate from the CLI archive and its
`SHA256SUMS`, which the installer verifies.

Automatic prereleases explicitly dispatch **Release package** with the verified
published tag. Releases created with `GITHUB_TOKEN` do not trigger the
`release: published` event, so this handoff is required to build the desktop
assets and publish their update feeds. Platform builds run separately from the
edge publication queue. The packaging workflow validates that the tag belongs
to `main`, the release is published, and its prerelease flag matches its version.
It can also be dispatched with a published tag to recover missing packaging.

### Signing

To set the Apple secrets, run `desktop/scripts/set-apple-secrets.sh` on a Mac
that has the Developer ID certificate; it checks the credentials with Apple
before storing them and prints nothing secret.

macOS builds are signed and notarized only when these repository secrets are
set: `APPLE_CERTIFICATE` (base64 Developer ID Application `.p12`),
`APPLE_CERTIFICATE_PASSWORD`, `APPLE_SIGNING_IDENTITY`, `APPLE_ID`,
`APPLE_PASSWORD` (an app-specific password), and `APPLE_TEAM_ID`. Without them
the DMG is unsigned and Gatekeeper blocks it, so don't announce it as an
end-user download. Windows builds are unsigned until a code-signing certificate
is configured; SmartScreen warns on them.

### App updates

Tag.app updates itself from signed update bundles. Each release also attaches
`Tag-VERSION-macos.app.tar.gz`, the Windows installer, and the AppImage with
`.sig` signatures, and the `desktop-updates` job writes
`tag-app-{stable,beta,alpha}.json` to the `channels` release. A build follows
its own line: alpha builds also receive newer betas and stable releases. A
manifest never moves to an older version, and a platform whose build failed
gets no update from that release.

Updates are signed with the key in the `TAURI_SIGNING_PRIVATE_KEY` and
`TAURI_SIGNING_PRIVATE_KEY_PASSWORD` secrets; the public half is in
`desktop/src-tauri/tauri.conf.json`. Keep an offline backup of the private key
and its password. If the key is lost, installed copies can't accept updates
signed with a new key; people must download Tag.app again once.

Before a stable release, qualify
Tag.app on each platform: first-run install, adding a Tag, start and stop from
the window and the tray, Keep Tags running across a logout, upgrade, and an
app update from the previous release.
