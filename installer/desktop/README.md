# Tag.app

The desktop app for macOS, Windows, and Linux, built with
[Tauri](https://tauri.app). It installs Tag on first run, then lists, starts,
and adds Tags. It does no Tag work itself: everything goes through the `tag`
command's JSON interface, described in
[`docs/reference/app-protocol.md`](../../docs/reference/app-protocol.md).

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
npx tauri dev          # the real app, driving your installed tag
```

`npx tauri dev` uses your real Tags. To try it against this checkout's CLI
instead, point `TAG_CLI` at a wrapper script, and set `TAG_HOME` to a scratch
folder to keep your real Tags out of it. `TAG_INSTALLER_SOURCE=/path/to/repo`
makes the first-run installer install that checkout. `TAG_INSTALLER_DEMO=1`
plays sample data in the real app; nothing changes.

`node scripts/screens.mjs OUT_DIR` saves a screenshot of every screen, light
and dark, from `npm run dev`.

## Build

```sh
npx tauri build        # Tag.app and .dmg, .msi and setup.exe, or .deb and .AppImage
```

The build copies `install.sh`, `install.ps1`, and `scripts/tag_install.py`
from the same commit into the app (`npm run bundle-installer`), so Tag.app
installs with reviewed code and passes `--channel` explicitly. The installer
still verifies each release's checksum and provenance.

Release builds need signing credentials in CI: a Developer ID certificate and
notarization credentials for macOS, and a code-signing certificate for
Windows. See [`RELEASE.md`](../../RELEASE.md).

## Upgrading from the Swift Tag.app

The app keeps the bundle identifier `team.hover.tag`. On first launch it reads
the Swift app's saved list of Tags to restore at login. If that list exists, it
turns on `tag autostart`, so the CLI's login service keeps those Tags running,
even with the app closed.
