# Tag.app

The desktop app for macOS, Windows, and Linux, built with
[Tauri](https://tauri.app). It installs Tag on first run, then lists, starts,
and adds Tags. It does no Tag work itself: everything goes through the `tag`
command's JSON interface, described in
[`docs/reference/app-protocol.md`](../docs/reference/app-protocol.md).

- `src/` — the window (React and TypeScript). `src/lib/` holds the protocol
  parsing and state, with no UI, and is unit-tested.
- `src-tauri/` — the native side (Rust): runs `tag`, streams setup and the
  installer, draws the menu bar or tray menu, hides to the tray when the
  window closes, and opens at login.
- `../protocol/examples/` — payloads shared with the CLI's contract tests.

## Develop

Needs Node 22 and a Rust toolchain. On Linux, also install
`libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev`.

```sh
npm install
npm run dev            # the window in a browser, with sample data
npm test               # protocol and state tests
npm run tauri -- dev          # the real app, driving your installed tag
```

`npm run tauri -- dev` uses your real Tags. To try it against this checkout's CLI
instead, point `TAG_CLI` at a wrapper script, and set `TAG_HOME` to a scratch
folder to keep your real Tags out of it. `TAG_INSTALLER_SOURCE=/path/to/repo`
makes the first-run installer install that checkout. `TAG_INSTALLER_DEMO=1`
plays sample data in the real app; nothing changes.

`node scripts/screens.mjs OUT_DIR` saves a screenshot of every screen, light
and dark, from `npm run dev`.

## Build

```sh
npm run tauri -- build        # Tag.app and .dmg, setup.exe, or .deb and .AppImage
```

The build copies `install.sh`, `install.ps1`, and `scripts/tag_install.py`
from the same commit into the app (`npm run bundle-installer`), so Tag.app
installs with reviewed code and passes `--channel` explicitly. The installer
still verifies each release's checksum and provenance.

Release builds need signing credentials in CI: a Developer ID certificate and
notarization credentials for macOS (`scripts/set-apple-secrets.sh` stores
them), and a code-signing certificate for Windows. Update bundles are signed
with the updater key in `TAURI_SIGNING_PRIVATE_KEY`. See
[`RELEASE.md`](../RELEASE.md).

Tag has one product version, sourced from the root `VERSION` file. The npm
build, development, and Tauri commands synchronize the desktop package and
Rust crate metadata automatically. Release builds use `RELEASE_TAG` for the
immutable release number; do not bump desktop versions separately.

Settings shows one version and one **Update Tag** action. It checks the saved
CLI release channel (or exact version pin), requires a matching signed desktop
release, updates the runtime, verifies its version, then updates and restarts
the desktop app when needed. An interrupted update can be retried; existing
settings and update policy are preserved. Missing desktop artifacts never
produce a false “up to date” result. Checks run every six hours; installation
always waits for a click.

**Release channel** in Settings switches between Stable, Beta, and Alpha. It
previews the switch with `tag upgrade --channel CHANNEL --dry-run --json`, says
what will happen, and on confirmation runs `tag upgrade --channel CHANNEL
--json`, which saves the choice for the CLI too. The desktop app then checks
that channel's `tag-app-CHANNEL.json` feed, so the app and runtime always land
on the same release. Switching to a channel whose newest release is older keeps
the installed release until the channel catches up. Tag.app has no `edge` feed,
so edge stays a terminal-only choice; Settings explains this when the CLI
follows edge.

**AI & models** in Settings shows each set-up Tag's Codex and Claude
connections and its default model, using `tag NAME settings ai … --json` (see
[AI connections](../docs/reference/app-protocol.md#ai-connections)). Sign-ins
run through the same streaming session as setup, so they report progress and
can be cancelled; the app never handles credentials. The section appears only
when the installed Tag reports the `ai-connections` capability. First-run setup
draws the CLI's `default_model` question as a grouped model picker with a
sign-in link for an agent that isn't connected, and shows connection cards
(`ai_connection`) only while nothing is connected. `tag settings` → AI & models offers the same
choices in a terminal.

**Home** reads `tag list --json` every 30 seconds. Each row shows the Tag's
`default_model_name` and `default_effort` after its name, then its
`description`. The quiet line under the sky comes from two slower checks: each
Tag's `tag NAME settings ai --json` every five minutes (an AI that can't answer,
grouped by cause) and `tag NAME logs --json` → `activity` every minute (the
latest reply). Both need the `ai-connections` and `logs-activity`
capabilities; without them Home shows the greeting. Nothing is invented: a
reply appears only once Tag recorded one.

**Tag detail** opens from a Home row at 800 px wide (`fitWindow` sets the
width too). It reads `channels` from `tag list --json`, the Tag's `activity`
from `tag NAME logs --json` every 30 seconds, and the model and thinking level
from `tag NAME settings ai --json` and `models --json`; saving runs
`tag NAME settings ai model VALUE --effort LEVEL`, with `--restart` only after
Save and restart. Copy full log copies the services' recent output.

**Add a Tag** draws `tag setup --json` (or `tag add --json`) in setup's own
order: `profile` (the `profile_picture` kind: name, description, Shuffle,
Upload through the file picker or a dropped file), `default_model`,
`workspace` (organizations open in place), `approve_setup`, the app-creation
`progress` steps, and `channels`. The finished result's `ready` gives the
Ready screen its Slack deep links. The step track's marker is the Tag's
picture; `existing_app` and `app_checks` switch it to the existing-app track.

**Look.** The window uses the Hover style from hover.team: its sky, frosted
panel and navy button. Source Sans 3 is bundled in `src/assets/fonts`
(SIL Open Font License), and the pixel art in `src/assets/art` comes from the
Tag cast in `assets/characters`. `prefers-color-scheme` switches day and night,
and `prefers-reduced-motion` stops the clouds, stars and sprites. On macOS the
window buttons sit in the sky (`titleBarStyle: Overlay`), and the sky is the
window's drag area. `?tags=N` in `npm run dev` shows Home with fewer sample
Tags, and `?update=1` offers a sample update.

The standalone CLI keeps `tag upgrade` for installations without the desktop
app. It uses the same product release number and existing migrations. If the
CLI was updated separately, the desktop's next update completes alignment.

## Upgrading from the Swift Tag.app

The app keeps the bundle identifier `team.hover.tag`. On first launch it reads
the Swift app's saved list of Tags to restore at login. If that list exists, it
turns on `tag autostart`, so the CLI's login service keeps those Tags running,
even with the app closed.
