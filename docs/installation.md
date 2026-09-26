# Installation and TAG home

TAG installs independently of any Git checkout. Its default home is:

| Platform | Home |
| --- | --- |
| macOS | `~/Library/Application Support/Tag` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/tag` |
| Windows | `%LOCALAPPDATA%\Tag` |

Set `TAG_HOME` to an absolute path before installing to choose another home.
Use an isolated `TAG_HOME` for development. WSL uses the Linux layout.

`TAG_HOME` always names the installation root. In a normal installation, each
Tag keeps its working files and private data together in `~/Tag/NAME`.
The built-in Tag uses `~/Tag/default`. All Tags share installed releases,
launchers, backend account authentication, and managed MFS.

```text
~/Tag/default/                       # everything owned by the default Tag
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

Named Tags use the same layout at `~/Tag/NAME`. `tag paths --json` reports
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
agent running under the same account; see [the security policy](../SECURITY.md).
Global Codex/Claude sign-ins and the shared MFS index remain outside this folder.

Configuration is JSON on every platform and is never executed as shell code.
POSIX installations create private directories; Windows uses the account's ACL.
MFS retains its own existing data and authentication locations, as do external
tools such as `gws`. The installer does not relocate their credentials.

## Install from a checkout

On supported macOS/Linux systems, install and sign in to Codex separately, then
run the installer below. Tag automatically prepares Python and Slack CLI; neither
Python nor uv needs to be on PATH. Setup checks the selected Codex transport and
`codex login status`, and explains how to update or sign in when needed. Tag does
not install Codex or change its global configuration or credentials.

Native Windows retains its Python 3.10+ and Slack CLI prerequisites; WSL uses the
Linux bootstrap. No local administrator privileges are needed. Slack sign-in
and any workspace administrator approval remain explicit user actions.

macOS/Linux:

```sh
./install.sh
tag setup
tag start
```

Windows PowerShell:

```powershell
./install.ps1
tag setup
tag start
```

The installer prints the command directory. Add `~/.local/bin` on macOS/Linux
or `%LOCALAPPDATA%\Tag\bin` on Windows to your user PATH if it is not already
present. It does not rewrite shell profiles or Windows PATH. Use `--bin-dir`
(PowerShell: `-BinDir`) to choose a different command directory. Existing
unrelated commands are never replaced.
If `tag` is a symlink to a recognized legacy Tag checkout, the installer safely
replaces that symlink with the managed launcher. The old checkout is left intact.

Each release has its own Python environment with the pinned runtime requirements.
Installation also downloads and validates MFS's default local embedding model
into its reusable cache, so the first `tag start` does not wait for a cold model
download. Later installs reuse the cached model.
The MFS Python server and matching MFS CLI are installed into Tag's managed
runtime on macOS and Linux. Google Workspace CLI and third-party MCP packages are
optional integrations, installed and authenticated separately.

The installer keeps dependency-manager output behind a concise Install screen.
Use `tag paths` for a readable location summary and `tag paths --json` for exact
machine-readable paths. `tag start` and `tag doctor` group checks into runtime,
configuration, memory, agent, and Slack rather than printing every successful
API probe; failed checks remain visible with their recovery action.

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

## Download installer

These endpoints become usable after this implementation is merged and a release
with `tag-<version>.zip`, `SHA256SUMS`, and `BUILD-PROVENANCE.json` has been
published. The bare command follows the default in `release-channels.json`, which
is always `stable`. It does not fall back to a prerelease when no stable release
exists; alpha, beta, and edge installations must select their channel explicitly:

```sh
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/klovr-co/hover-tag/main/install.sh | sh

# Choose an update channel explicitly.
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/klovr-co/hover-tag/main/install.sh | sh -s -- --channel beta
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/klovr-co/hover-tag/main/install.sh | sh -s -- --channel alpha
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/klovr-co/hover-tag/main/install.sh | sh -s -- --channel edge

# Reproduce one immutable release.
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/klovr-co/hover-tag/main/install.sh | sh -s -- --version 0.2.0-beta.1
```

```powershell
$installer = Join-Path $env:TEMP 'tag-install.ps1'
Invoke-WebRequest https://raw.githubusercontent.com/klovr-co/hover-tag/main/install.ps1 -OutFile $installer
& $installer -Channel beta
# Or: & $installer -Version 0.2.0-beta.1
```

`stable`, `beta`, and `alpha` each follow only releases from their named phase;
`edge` follows the latest successful `main` build. Switching to a phase whose
latest release is older than the installed version requires `--allow-downgrade`.
The selected channel, installed version, source commit, and check time are stored
atomically in `current.json`; rollback restores the previous record with the
previous release.

Release downloads are checked against both the SHA-256 manifest and build
provenance before extraction. This checks integrity and consistency; it is not an
independent publisher signature. The HTTPS bootstrap script and GitHub repository
remain trust inputs.

## Integrations

Place Tag-specific Codex skills in `~/Tag/NAME/.agents/skills/<name>/SKILL.md`.
The installer creates TAG's administration skill there; upgrades preserve
existing skill directories, including locally installed skills and edits.
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

The Slack settings UI layers these three defaults over the matching values in
the user's global `~/.codex/config.toml`; omitted values continue to inherit the
global setting. Saved Slack-user choices take precedence. Restart Tag after
editing this file so the bridge reloads the model catalog and defaults.

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

Claude remains experimental. It runs from the same stable workspace, with
project skills under `.claude/skills` and normal Claude project MCP settings in
`.mcp.json`; use Claude's own trust/approval setup for project MCP servers.

Codex discovery references: [skills](https://developers.openai.com/codex/skills)
and [MCP](https://developers.openai.com/codex/mcp).

## Operate, upgrade, and migrate

Run `tag` in a terminal for a menu based on the current installation state.
`tag setup` resumes missing answers; `tag config` edits individual settings.
Skills use `tag inspect --json` and `tag doctor --json` to plan the same steps.
See [setup and management](tag-management.md) for the shared flow and commands.

`tag paths` shows storage locations in a readable view; `tag paths --json`
provides the same data for automation. `tag doctor` checks configuration and
connectivity. `tag start` runs in the background until stopped or rebooted.
Use `tag status`, `tag logs`, and `tag stop`. Put `NAME` before the command to
operate a named Tag, such as `tag personal status`; `tag list` shows all independent configurations. The dedicated `tag restart`
command presents one operation and should be preferred to manually chaining
stop and start. Automatic login startup is not
configured. A separately managed MFS server is reused and never stopped by TAG.

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

To uninstall, stop TAG, remove its managed command, and remove the selected TAG
home after backing up any configuration and personal skills you want to keep.
Global backend configuration, external integration credentials, and MFS data
are separate and remain untouched.

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

| Component | Download payload | Installed logical bytes |
| --- | ---: | ---: |
| Initial shell installer | 5,581 B | 5,581 B |
| uv | 16,988,553 B | 37,194,640 B |
| Python | approximately 23.9 MiB reported by uv | 69,627,389 B |
| Slack CLI | 7,607,363 B | 20,527,232 B |
| Runtime packages (118 wheels) | 196,784,133 B | included in release environment below |
| MFS CLI | 2,038,676 B | included in release environment below |
| Release environment, including packages and MFS CLI | see above | 583,249,864 B |
| MFS embedding model and tokenizer | 587,042,498 B | 587,042,939 B including cache metadata |

The dependency payload is approximately 797 MiB before the Tag source archive,
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
