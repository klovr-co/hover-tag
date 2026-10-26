# Tag.app add-on interface

An add-on adds screens and controls to Tag.app. It fills only the fixed
places listed here, gets Tag data only through the `tag … --json` commands of
the [app protocol](app-protocol.md), and loads only when its provider's
signature checks out.

This page describes **add-on interface version 1**. The interface is versioned
on its own, separately from the app protocol.

## Rules

- **Add-only.** An add-on adds screens, folders, buttons and switches. It
  never replaces or hides a built-in screen, and no place lets it. A future
  interface version may add new kinds of places; add-ons made for an earlier
  version keep working where Tag.app still supports that version.
- **Tag data through the protocol.** The add-on's code gets a `host` object.
  `host.tag(args)` runs `tag ARGS` only for commands the manifest lists in
  `tag_commands`, and only with `--json`. The add-on gets no file access and
  no other way to reach a Tag's folder.
- **Signed.** Tag.app checks the package's Ed25519 signature against the
  provider's configured public key, then the code's SHA-256 against the signed
  manifest, then the manifest's `interface` version. If any check fails,
  nothing from the package runs. The checks run every time the add-on loads,
  not only when it is installed.
- **One line in Settings.** Each configured provider gets one line in
  **Settings → General**: "Connect {name}", then who is connected. Nothing is
  greyed out or locked before an add-on is connected.
- **No sandbox yet.** The add-on's code runs in the app's window. Only
  providers configured in the build are trusted, and only their signed code
  runs. A sandbox comes before Tag.app accepts add-ons from anyone else.

## Providers

A build lists the providers it trusts in `VITE_TAG_ADDON_PROVIDERS`, a JSON
list:

```json
[{
  "id": "example",
  "name": "Example",
  "description": "Sign in once to add Example's screens to Tag.app.",
  "url": "https://addons.example.com",
  "public_key": "BASE64 OF THE 32-BYTE ED25519 PUBLIC KEY"
}]
```

Tag.app's content security policy only allows requests to its own origins. A
build that names a provider adds the provider's origin to `connect-src`, for
example with `tauri build --config` overriding `app.security.csp`. Add-on code
loads from a `blob:` URL after the checks, which `script-src` allows.

## Connecting

Connecting signs in once through the browser, like a device sign-in. All
paths are under `{url}/tag-addon/v1`.

1. `POST /connect` with `{"client": "tag-app", "app_version", "interfaces": [1]}`
   returns `{"device_code", "verification_url", "interval", "expires_in"}`.
2. Tag.app opens `verification_url` in the browser, where the person signs in
   and approves this computer.
3. Tag.app polls `POST /connect/token` with `{"device_code"}` every `interval`
   seconds. The reply is `{"status": "pending"}`, `{"status": "denied"}`,
   `{"status": "expired"}`, or
   `{"status": "approved", "token", "account": {"label"}}`.
4. `GET /package` with `Authorization: Bearer TOKEN` returns the package.
   Tag.app checks it before keeping it, and checks for a newer package each
   time it starts.
5. **Disconnect** removes the package and token from this computer and calls
   `POST /disconnect` with the token.

The token and package are kept in the app's own storage on this computer.

## Package

```json
{
  "format": "tag-addon/1",
  "manifest": "THE MANIFEST, AS JSON TEXT",
  "code": "THE ENTRY MODULE, AS JAVASCRIPT TEXT",
  "signature": "BASE64 ED25519 SIGNATURE"
}
```

The signature covers the UTF-8 bytes of `tag-addon-manifest/1\n` followed by
the manifest text. The manifest is kept as text so the signed bytes are exact.

## Manifest

See `protocol/examples/addon-manifest.json`. Unknown fields are refused, so an
add-on can't ask for a place this version doesn't have.

| Field | Meaning |
| --- | --- |
| `interface` | Add-on interface version: `1` |
| `id`, `name`, `version` | The add-on |
| `publisher` | The provider `id` it is signed by |
| `entry_sha256` | SHA-256 of `code`, lowercase hex |
| `tag_commands` | `tag` commands it may run, such as `list` or `threads` |
| `screens` | Sidebar screens: `id`, `title`, `icon` |
| `knowledge_folders` | Folder groups in a Tag's Knowledge tree: `id`, `title` |
| `buttons` | Named buttons: `id`, `place`, `label` |
| `channel_switches` | One switch per channel: `id`, `label`, `detail` |

Button places in version 1:

| Place | Where | Target |
| --- | --- | --- |
| `thread.actions` | Under a thread's details, in Activity | `{tag, channel, thread_ts}` |
| `tag.actions` | In a Tag's header, next to its on switch | `{tag}` |

Room to draw in version 1:

- An add-on screen opens in a 1040 × 680 window. Its content scrolls inside,
  so a long screen never makes the window taller.
- While a Tag's Knowledge tab is open, the window is 1040 wide, leaving about
  500 points for the selected folder next to the folder list.

## Code

The entry module exports `activate(host)`, which returns the implementation
for each id in the manifest. Ids the manifest doesn't declare are ignored.

```js
export function activate(host) {
  return {
    screens: { reports: { mount(element) { element.textContent = "Hello"; return () => {}; } } },
    knowledge_folders: { shared: {
      async list({ tag }) { return [{ id: "docs", title: "Docs", icon: "📁", detail: "3 files" }]; },
      mount(element, { tag, folder }) { /* draw the folder */ },
    } },
    buttons: { "open-web": { run({ tag, channel, thread_ts }) { return host.open("https://…"); } } },
    channel_switches: { sync: {
      async get({ tag, channel }) { return false; },
      async set({ tag, channel }, on) { /* save it */ },
    } },
  };
}
```

`mount(element, context)` draws into the element and may return a clean-up
function. It is framework-free on purpose: an add-on brings its own. If it
throws, Tag.app shows the error in place of the screen and keeps working.

| `host` | |
| --- | --- |
| `interface` | `1` |
| `app` | `{version, platform}` |
| `account` | `{label}` of the person who connected |
| `tag(args)` | Runs a listed `tag` command with `--json`; others return exit code 2 without running |
| `request(path, init)` | A request to the provider's `url` + `path`, with the token |
| `open(url)` | Opens an http(s) page in the browser |
| `say(text)` | A short message at the bottom of the window |
