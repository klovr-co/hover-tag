# Installation details

This page is for operators, developers, and coding agents. Most people only
need [Install Tag](../installation.md).

## Install from a terminal

The terminal installer installs the same `tag` command without the app.

macOS and Linux:

```sh
curl -fsSL https://hover.team/tag/install | sh
```

Windows PowerShell (5.1 or later):

```powershell
$installer = Join-Path $env:TEMP 'tag-install.ps1'
Invoke-WebRequest https://raw.githubusercontent.com/klovr-co/hover-tag/main/install.ps1 -OutFile $installer
& $installer
```

The installer prints the command directory: `~/.local/bin` on macOS and Linux,
or `%LOCALAPPDATA%\Tag\bin` on Windows. If your terminal can't find `tag`, add
that directory to your PATH. The installer doesn't edit shell profiles or the
Windows PATH. Use `--bin-dir` (PowerShell: `-BinDir`) to choose another
directory. It never replaces an unrelated command with the same name.

Then set up and start your first Tag:

```sh
tag setup
tag start
```

`tag setup` asks the same questions as the desktop app, in the same order. Unlike
the app, it leaves the Tag stopped until you run `tag start`. Run `tag autostart on` to keep your Tags
running after you sign in to your computer. See
[Set up and manage Tag](../tag-management.md).

From a source checkout, run `./install.sh` (Windows: `./install.ps1`) instead
of downloading the script. It installs the checkout's code the same way.

## Set up with your coding agent

Codex or Claude Code can install and set up Tag for you with the
`hover-tag-setup` skill. This needs [Node.js and npm](https://nodejs.org/en/download):

```bash
# Codex
npx skills add klovr-co/hover-tag --skill hover-tag-setup -a codex -g
# Claude Code
npx skills add klovr-co/hover-tag --skill hover-tag-setup -a claude-code -g
```

Open a new session and ask:

```text
Use the hover-tag-setup skill to set up Tag for me.
```

The agent runs the terminal installer, proposes your setup choices together,
and drives `tag setup`. You still sign in to Slack and approve the app
yourself. See [Set up Tag](../getting-started/first-task.md).

## Upgrade migrations from v0.2

Upgrade a v0.2 installation with `tag upgrade --channel beta` during the 0.3
beta, or `tag upgrade` once 0.3 is stable.

your Tags.

You don't need to repeat setup. The first `tag start` after the upgrade (or the
restart that `tag upgrade` does) runs these migrations before the Tag
connects. Each one is recorded only after Tag checks the result, and a failed
one is retried on the next start:

| What changes | What you see |
| --- | --- |
| Your first Tag, `default`, is renamed after its Slack workspace and app IDs. `~/Tag/default` moves to `~/Tag/t0abc123-a0xyz789`, and it becomes the main Tag. | `tag list` shows the new name. Commands without a name still use it. |
| On Windows, Tag installs its own Python and reinstalls the release with it. | Nothing to do. The old runtime stays in use if this fails. |
| The Slack app gets the release's new permissions and settings, using your existing Slack sign-in. | Usually nothing. If Slack needs a fresh sign-in or an administrator's approval, `tag start` says exactly what to do. |
| `team:read` is requested so the app can show the workspace icon. It is optional. | If it isn't approved, the workspace shows as a letter and the Tag still starts. Tag asks again at most once a day. |
| Saved deliverables are attached to Slack as well as saved locally, unless you chose otherwise. | Files Tag saves also appear in the thread. |
| Model choices that people made in Slack's **Configure** view are archived. That view is gone. | Each Tag has one model and thinking level for every request. Change it in the app's **Details** tab or with `tag NAME settings ai model`. |
| Channel names are saved for activity views. | The app and `tag logs --activity` show channel names. |

Tag also keeps your release channel. Once you use the app, it and the CLI
follow the same channel.

After the upgrade, if a Tag fails to restart, `tag upgrade` prints one line
for each failed Tag with the cause and a `tag NAME doctor` command. See
[Troubleshooting](../troubleshooting.md#after-an-upgrade).

## Where Tag keeps files


Tag installs independently of any Git checkout. Its default home is:

| Platform | Home |
| --- | --- |
| macOS | `~/Library/Application Support/Tag` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/tag` |
| Windows | `%LOCALAPPDATA%\Tag` |

Set `TAG_HOME` to an absolute path before installing to choose another home.
Use an isolated `TAG_HOME` for development. WSL uses the Linux layout.

`TAG_HOME` always names the installation root. In a normal installation, each
Tag keeps its working files and private data together in `~/Tag/NAME`.
Each Tag is named after its Slack workspace and app IDs, for example
`~/Tag/t0abc123-a0xyz789`. All Tags share installed releases,
launchers, backend account authentication, and managed MFS.

```text
~/Tag/t0abc123-a0xyz789/             # everything owned by one Tag
  your-files.md
  artifacts/CHANNEL_ID/             # new saved deliverables by Slack channel
  .agents/skills/                    # Codex skills
  .codex/config.toml                 # Tag-only Codex configuration
  .claude/skills/                    # Claude skills
  .mcp.json                          # optional Claude project MCP definitions
  .tag/                             # private, excluded from Git
    instance.json
    config/                         # settings and credentials
    integrations/                   # connector definitions and optional tools
    state/                          # conversation state, logs, process records
    tmp/                            # temporary files

~/Library/Application Support/Tag/   # macOS installation-wide software/services
  releases/<version>-<installation-id>/
  current.json
  previous.json
  bin/
  shared/mfs/
  state/
```

Every Tag uses the same layout at `~/Tag/NAME`. `tag paths --json` reports
the exact locations. An explicit non-standard `TAG_HOME` retains its portable
layout: private data under `instances/NAME` and files under
`instances/NAME/workspace`.

On startup, Tag automatically migrates older root-level and per-instance data
into the working folder's `.tag` directory before loading settings or checking
readiness. It stops the affected bridge, preserves the original files, updates
managed paths and connector credential references, and verifies the copy before
switching to the new home. Conflicts preserve both versions and report the
exact file to resolve; interrupted migrations retry on the next start. Existing
working files and unrelated settings are preserved. No setup or sign-in is
required solely for this move.

The hidden `.tag` folder contains secrets. Keep it when moving a Tag's working
folder, and treat copies as private. Cloud syncing the folder also syncs those
secrets. Owner-only permissions and Git exclusion do not prevent access by an
agent running under the same account; see [the security policy](../../SECURITY.md).
Global Codex/Claude sign-ins and the shared MFS index remain outside this folder.

Configuration is JSON on every platform and is never executed as shell code.
POSIX installations create private directories; Windows uses the account's ACL.
MFS retains its own existing data and authentication locations, as do external
tools such as `gws`. The installer does not relocate their credentials.

## Run from source

Contributors can prepare the checkout's local runtime explicitly:

```sh
./install.sh --dependencies-only
./tag status
./tag dev
```

On Windows, use `./install.ps1 -DependenciesOnly` followed by `./tag.cmd status`.
Source preparation builds and checks an environment under `.runtime` before
atomically selecting it through `.venv`; incomplete environments are not selected.
The source launcher only uses that checkout's `.venv`; it never falls back to a
system Python. Managed installations automatically finish required runtime
migrations before startup readiness checks. For normal use, prefer the
managed installer above so upgrades and runtime dependencies remain pinned.
`./tag dev` starts the normal dependencies, watches Python source, reloads only
the Slack bridge when files change, and shows bridge logs in the foreground.
For a loopback `MFS_URL`, it owns the MFS process and Ctrl-C stops both services.
A configured remote MFS endpoint remains externally managed.

`./tag app` opens the desktop app from the same checkout, driving this checkout's CLI
instead of an installed one. It needs Node 22 and a Rust toolchain, installs the
app's dependencies when needed, and refuses to run while the installed app is
open, because only one copy of the app runs at a time. Set `TAG_HOME` to a scratch folder
to try setup without touching your real Tags.

## Download integrity

Release downloads are checked against both the SHA-256 manifest and build
provenance before extraction. This checks integrity and consistency; it is not an
independent publisher signature. The HTTPS bootstrap script and GitHub repository
remain trust inputs. Desktop app update bundles are separately signed.

## Integrations

Place Tag-specific Codex skills in `~/Tag/NAME/.agents/skills/<name>/SKILL.md`.
Put Claude skills in `~/Tag/NAME/.claude/skills/<name>/SKILL.md`. Upgrades
preserve your skill directories and edits. They remove only an unedited copy of
the old `open-tag-admin` skill that earlier releases installed.
Global backend skills and authentication remain available, subject to the
backend's own discovery rules and context limits.

Tag releases also bundle the `tag-troubleshoot` skill in the immutable runtime
so an upgrade makes the recovery handoff available to existing installations.
It is copied from the release source allowlist; the installer does not overwrite
user-owned workspace skills. Use it only when a coding agent has access to the
machine running Tag, and follow its manual contribution and Slack-app deletion
boundaries.

Put Tag-specific Codex defaults and MCP definitions in
`~/Tag/NAME/.codex/config.toml`:

```toml
model = "gpt-5.6-sol"
model_reasoning_effort = "high"
service_tier = "default"

[mcp_servers.example]
command = "example-mcp-server"
args = ["--stdio"]
env_vars = ["EXAMPLE_API_TOKEN"]
```

These values layer over the matching values in the user's global
`~/.codex/config.toml`; omitted values continue to inherit the global setting.
A model and thinking level chosen for the Tag in the app or with
`tag NAME settings ai model` apply to every request and take precedence.
Restart Tag after editing this file so the bridge reloads the model catalog and
defaults.

TAG passes MCP definitions to Codex for that invocation only. Other project
config keys in this file are not applied by TAG. Use distinct server names;
matching names override the corresponding global server fields. Names must
contain only letters, numbers, underscores, or hyphens. Prefer absolute paths
for local MCP server executables and arguments. Put integration commands in
`integrations/bin` when they should be available only to TAG.

Use environment references (`env_vars`, `bearer_token_env_var`,
`env_http_headers`) for MCP secrets. Inline values would become process arguments.
Configure OAuth with the backend's supported login flow. TAG does not grant
access merely by adding a server definition; normal backend permissions apply.

Claude runs from the same stable workspace through
the Claude Agent SDK, with project skills under `.claude/skills`. Tag passes
the workspace `.mcp.json` servers to each Claude run, mirroring how it layers
`.codex/config.toml` MCP servers for Codex.

Codex discovery references: [skills](https://developers.openai.com/codex/skills)
and [MCP](https://developers.openai.com/codex/mcp).

## Operate, upgrade, and migrate

Run `tag` in a terminal for a menu based on the current installation state.
`tag setup` resumes missing answers; `tag config` edits individual settings.
Skills use `tag inspect --json` and `tag doctor --json` to plan the same steps.
See [setup and management](../tag-management.md) for the shared flow and commands.

`tag paths` shows storage locations in a readable view; `tag paths --json`
provides the same data for automation. `tag doctor` checks configuration and
connectivity. `tag start` runs in the background until stopped or rebooted.
Use `tag status`, `tag logs`, and `tag stop`. Put `NAME` before the command to
operate a named Tag, such as `tag personal status`; `tag list` shows all independent configurations. The dedicated `tag restart`
command presents one operation and should be preferred to manually chaining
stop and start. `tag autostart on` keeps wanted Tags running after login; see
[Keeping Tags running](cli-operations.md#keeping-tags-running). A separately managed MFS server is reused and never stopped by TAG.

Run `tag upgrade` after the initial installation. It follows the saved channel,
downloads and verifies the candidate, stages a separate runtime, atomically
selects it, and restarts running Tag services. Failed verification or dependency
installation leaves the active release unchanged. `tag upgrade --dry-run`
reports the verified target without changing the installation; add `--json` for
automation. Use `--channel stable|beta|alpha|edge` to change channels or
`--version X.Y.Z` to install and pin an exact release. `--no-restart` leaves
running services on the old code until `tag restart` is run. Upgrades never
install an older semantic version by default. A channel change is saved while
Tag keeps the newer installed release until that channel catches up. An
intentional older install requires `--allow-downgrade`; prefer `tag rollback`
when returning to the immediately previous known-good release. `tag rollback`
selects the previous release only after all Tag bridges and the
installation-owned shared MFS service are stopped with `tag memory stop`.

Channel selection reads a public `tag-release-channels.json` index from the
moving `channels` GitHub release, then downloads immutable numbered assets
directly. Public installation therefore does not require GitHub authentication
and does not normally consume the anonymous REST API quota. Tag falls back to
the GitHub Releases API if the index cannot be fetched or validated during
rollout, while checksum and provenance verification remain mandatory.

Human-readable `tag`, `tag status`, `tag inspect`, and successful `tag setup`
and `tag start` runs also check the saved channel at most once every 24 hours.
When a newer release is published they show a non-fatal `tag upgrade` reminder;
offline or failed checks never prevent the requested command. Source installs
without a saved channel are compared with the default alpha channel; when a
newer numbered release exists, the suggested `tag upgrade --channel alpha`
command both installs it and saves the channel for future checks. The reminder
reads release metadata only; `tag upgrade` still downloads and checks the
release checksum and provenance before selecting it. JSON output never contains
reminder text.

Rollback is blocked while named Tags exist because an older selected CLI may
not understand their lifecycle; use a coordinated supported upgrade path
instead of mixing old and new lifecycle commands. Older releases remain
available; no automatic release deletion is performed.

For a legacy checkout, explicitly copy settings and local skills:

```sh
tag migrate --from /absolute/path/to/old/tag
```

This reads generated `export KEY=value` configuration as data, copies local
skills and MCP files, preserves existing destination settings/skills, and leaves
all originals untouched. Review copied MCP executable paths and `tag doctor`.
Old `.runtime` process records and logs are not migrated. Stop the old Tag
using its original launcher before starting the new installation. If its Slack
heartbeat is still current, the new `tag start` refuses to launch and identifies
the conflicting command instead of starting a second Slack connection.

To uninstall, see [Uninstall or reset](../installation.md#uninstall).

## Verification

The CI matrix covers macOS, Linux, and native Windows. Unit tests exercise
installation and upgrade persistence, failure before activation, migration,
process ownership, and traversal rejection. The installation smoke test installs
real dependencies and runs the installed command outside the checkout.
Live Slack and native backend qualification still require an authenticated test
on each target platform; an offline smoke test does not establish those results.

## Automatic dependency preparation

The POSIX bootstrap uses curl, tar, and SHA-256 verification before any Python
code runs. It reuses uv 0.12.19 or downloads that pinned version into
`TAG_HOME/runtime/uv`; it prefers an existing Tag-managed Python 3.12.14, then
an exact uv-managed Python already on the machine, otherwise downloads one into
`TAG_HOME/runtime/python`. uv verifies its pinned Python distribution checksums.
System Python and shell profiles are left alone. Paths containing spaces work;
activation of a virtual environment is not required.

The Windows bootstrap, `install.ps1`, does the same with `Invoke-WebRequest`
and `Get-FileHash`: the same pinned uv (x86_64 or ARM64) and Python 3.12.14,
under `TAG_HOME\runtime`. Installations made before this used a system Python;
on the next `tag start`, Tag prepares the private Python, reinstalls the
current release with it, and records `dependency_schema: 1`. If that fails, the
previous runtime stays selected and the next start retries.

Each release has a separate environment. The launcher records an explicit
interpreter path, so changing the default `python3` does not change Tag's runtime.
Old runtimes remain available for rollback. Preparation and runtime import checks
finish before `current.json` switches releases. Interrupted preparation can be
retried: incomplete downloads are never selected, and installation locks are
released by the operating system when the process exits.

Slack CLI 4.7 or newer in the 4.x line is reused when available. Otherwise Tag
downloads the pinned Slack CLI 4.8.0 for macOS/Linux ARM64 or x86-64, verifies its
SHA-256, extracts only the executable, checks that it runs, and atomically
publishes it under `TAG_HOME/runtime/slack/4.8.0`. Existing user-managed binaries,
Slack authorization directories, and app configuration are preserved. Preparation
does not sign in, create or delete an app, or expand permissions. Guided setup
continues directly to the existing authorization handoff.

Upgrades run the new release's bootstrap before activation. A versioned startup
migration also covers upgrades performed by older installers: it prepares a new
release environment, preserves the saved update policy and previous release,
and reloads the CLI before dependent checks. The migration checkpoint is written
only after validation; failures retain the active release and retry on startup.
Managed dependencies are shared installation resources, separate from each
Tag's working folder and `.tag` data. Native Linux builds require a compatible
glibc distribution and wheels for the runtime packages; musl-only distributions
are not qualified by this bootstrap.

## Measured installation size

Measured on macOS ARM64 on 2026-09-27, using Python 3.12.14, uv 0.12.19,
Slack CLI 4.8.0 and the current runtime requirements. These are measurements,
not size limits or promises for other platforms. Transitive package updates,
platform wheels, filesystem allocation, and existing caches change the totals.
Since this measurement, Tag no longer installs the MFS CLI (about 2 MB less).

| Component | Download payload | Installed logical bytes |
| --- | ---: | ---: |
| Initial shell installer | 5,581 B | 5,581 B |
| uv | 16,988,553 B | 37,194,640 B |
| Python | approximately 23.9 MiB reported by uv | 69,627,389 B |
| Slack CLI | 7,607,363 B | 20,527,232 B |
| Runtime packages (118 wheels) | 196,784,133 B | included in release environment below |
| MFS CLI (no longer installed) | 2,038,676 B | included in release environment below |
| Release environment, including packages and the MFS CLI then installed | see above | 583,249,864 B |
| MFS embedding model and tokenizer | 587,042,498 B | 587,042,939 B including cache metadata |

The dependency payload is approximately 797 MiB before the Tag runtime archive,
index metadata, HTTP overhead, or retries. The first installed Tag home measured
711,887,280 logical bytes; the model adds about 560 MiB outside that home, under
`${MFS_HOME:-~/.mfs}/onnx-cache`. The initial script is small because it downloads
these dependencies; it does not eliminate their transfer time or storage cost.
Allocated disk usage (`du -sk`) measured 73,076 KiB for Python, 36,324 KiB
for uv, 20,048 KiB for Slack CLI, 612,068 KiB for a prepared release environment,
and 588,776 KiB for the embedding cache.
The uv package cache separately measured 603,748,268 logical bytes. Do not add
that figure to physical disk usage: uv may share files with environments using
hardlinks or filesystem clones. Retaining releases for rollback also uses space.

For reproduction, install into an empty `TAG_HOME` with an empty `UV_CACHE_DIR`
and `UV_PYTHON_INSTALL_DIR`, and prepare the model with an empty `MFS_HOME`.
Hide uv from PATH to exercise its download; the bootstrap also works without
Python on PATH. Count regular files without following symlinks for logical
sizes, and use `du -sk` for allocated disk usage. Record the selected wheel names
from the uv cache and their PyPI release artifact sizes; the table uses those
payload sizes and upstream release asset sizes, not a network traffic estimate.
The model was separately downloaded into an empty cache and validated by MFS's
embedding probe. Repeat installation reused the managed tools and caches;
launch and rollback were verified with only system tools on PATH.

Upstream references: [uv Python management](https://docs.astral.sh/uv/concepts/python-versions/),
[uv pinned release](https://github.com/astral-sh/uv/releases/tag/0.12.19), and
[Slack CLI pinned release](https://github.com/slackapi/slack-cli/releases/tag/v4.8.0).

## Keeping release contents minimal

`scripts/runtime-files.json` is the explicit file list shared by the release
packager and installer. A new file is shipped only when it is added to that list.
This applies to both archive downloads and installations from a checkout.
Required missing files, unsafe paths, and symlinks fail packaging instead of
silently producing an incomplete release. CI installs the generated archive
outside the checkout and exercises the installed CLI and offline doctor.

The runtime includes setup, upgrades, migrations, the troubleshooting skill,
runtime contracts, operating guides, licensing, security, and privacy information.
Tests, CI workflows, release publishing tools, contributor skills, development
docs, repository agent settings, and branding assets stay in the source repository.
The README uses online branding and links to contributor material there.

Existing verified releases without the manifest remain installable through the
legacy layout. Upgrades prepare the new minimal release before activation and
retain the previous release for rollback; this change does not delete files from
old releases or alter operator data.

For the measured checkout, the archive fell from 6,603,360 bytes (196 files) to
approximately 280 KB (74 files), about a 96% reduction. This is a reduction in the
Tag archive, not in the Python packages or embedding model described above.
MFS 0.4.6 declares `markitdown[all]` as a required dependency, including document,
audio, and other converters. The installer keeps that supported dependency set;
trimming it requires a narrower upstream package and capability testing, rather
than omitting declared dependencies or installing with `--no-deps`.
