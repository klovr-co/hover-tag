# Qualifying Tag.app on Windows

Run this before a release claims Tag.app for Windows is supported, and
copy the filled-in record into that release's file in
[release evidence](../release-evidence/). Each item is **PASS** or **FAIL**
with the proof named in the item: an ID, a Slack timestamp, a version, or a
log line. A FAIL blocks the claim until it is fixed and that item is run
again.

CI already covers what doesn't need a person: the Windows unit tests, the
terminal installer started the way Tag.app starts it (install smoke), and
installing, updating and uninstalling the NSIS installer next to the runtime
(the desktop job).

## Setup

- A clean Windows 11 account that has never had Tag installed. Record the
  machine as **x64** or **ARM64**. ARM64 runs the x64 app under emulation.
- Give the account a user name with a non-ASCII letter, for example `Zoë`, to
  cover paths like `C:\Users\Zoë\AppData\Local\Tag`.
- Codex or Claude Code signed in, and access to the Slack sandbox with a test
  Tag app you may create.
- The previous stable release's `Tag-VERSION-windows-setup.exe`, for the
  update checks.

## Record

```markdown
## Tag.app on Windows

- Release: `Tag-VERSION-windows-setup.exe` from `vVERSION`
- Machine: Windows 11 VERSION, x64 or ARM64, physical or VM
- Evaluated: **YYYY-MM-DD**

1. Download: **PASS**: `sha256sum -c DESKTOP-SHA256SUMS-windows` matched.
2. SmartScreen: **PASS**: the only warning was "Windows protected your PC";
   **More info** → **Run anyway** opened the installer.
3. Install the app: **PASS**: installed without an administrator prompt to
   `%LOCALAPPDATA%\Tag\tag-desktop.exe`, with a Start menu shortcut.
4. Install Tag: **PASS**: **Install Tag** finished every step. `tag version`
   printed VERSION. The memory model finished downloading on Home.
5. Add a Tag: **PASS**: Tag `NAME` (app `A…`, workspace `T…`) set up with
   BACKEND and MODEL, started, and showed as online. No console window opened
   at any point.
6. Slack reply: **PASS**: marker `TAG-WIN-VERSION-YYYYMMDD` sent at Slack
   timestamp `…`; exactly one thread reply with the marker at `…`.
7. Memory: **PASS**: a question about an earlier message in the channel got
   an answer that used it, at Slack timestamp `…`.
8. Tray: **PASS**: closing the window kept Tag in the notification area.
   **Stop All Tags** and **Start All Tags** from the tray menu worked, and
   **Quit Tag** quit.
9. Keep running: **PASS**: with **Open Tag at login** and **Keep Tags running**
   on, signing out and back in brought the app and the Tag back without a
   click.
10. Upgrade: **PASS**: from PREVIOUS installed by its own setup, Tag
    upgraded with **Update Tag** to VERSION. The Tag restarted and answered
    marker `…` at `…`.
11. App update: **PASS**: the update installed in place without an
    administrator prompt, and the app reopened as VERSION.
12. Uninstall: **PASS**: removing Tag in **Settings** → **Apps** →
    **Installed apps** removed `tag-desktop.exe` and kept `%LOCALAPPDATA%\Tag\bin`,
    `home` and `releases`, and `~\Tag\NAME`. Installing again opened Home with
    the same Tag.
```

Check 10 and 11 together: in Tag.app, one **Update Tag** click updates your
Tags and then the app. To test them, install the previous stable release's
Tag.app first, run checks 3 to 6 with it, then update.

## What each check protects

| Check | Breaks when |
| --- | --- |
| 2 SmartScreen | The installer is signed differently, or the warning changes. The guide tells people what to click. |
| 3, 12 Install and uninstall | The app and the runtime share `%LOCALAPPDATA%\Tag`. A packaging change could delete runtime files. |
| 4 Install Tag | `install.ps1` runs from the app's `\\?\` resource path, downloads Python, uv and the Slack CLI, and starts memory. |
| 5 Add a Tag | Slack sign-in and the Slack CLI, which runs x64 on ARM64. Background runs must not open console windows. |
| 9 Keep running | Login start uses the per-user `Run` registry key. |
| 10, 11 Updates | The update feed's `windows-x86_64` entry and the NSIS `/UPDATE` install, while Tags are running. |
