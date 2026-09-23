# Installation and TAG home

TAG installs independently of any Git checkout. Its default home is:

| Platform | Home |
| --- | --- |
| macOS | `~/Library/Application Support/Tag` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/tag` |
| Windows | `%LOCALAPPDATA%\Tag` |

Set `TAG_HOME` to an absolute path before installing to choose another home.
Use an isolated `TAG_HOME` for development. WSL uses the Linux layout.

`TAG_HOME` always names the installation root. Every Tag, including `default`,
lives under `<TAG_HOME>/instances/NAME`, while its user-editable workspace lives
at `~/Tag/NAME`. All Tags share releases, launchers, backend account
authentication, and managed MFS.
Do not point concurrent old and new CLI releases at the same home while
upgrading the shared service ownership record.

```text
~/Library/Application Support/Tag/     # platform application-data home
  releases/<version>-<installation-id>/
  current.json
  previous.json
  bin/
  instances/
    default/
      instance.json
      config/settings.json
      integrations/bin/     # optional TAG-only tools; prepended to PATH
      state/                # bridge logs, identity, and conversation settings
      tmp/                  # disposable task files and generated artifacts
    NAME/                   # the same layout for each additional Tag
  shared/mfs/               # installation-owned MFS process state and logs

~/Tag/
  default/                  # user-owned agent workspace
    .agents/skills/         # Codex skills
    .codex/config.toml      # Tag-only Codex MCP definitions
    .claude/skills/         # Claude skills
    .mcp.json               # Claude project MCP definitions, when configured
  NAME/                     # workspace for each additional Tag
```

An explicit non-standard `TAG_HOME` remains self-contained and keeps workspaces
below `instances/NAME/workspace`; this preserves isolation for development,
testing, and portable installations.

Installations created before the uniform Tag layout may still have
`config/`, `workspace/`, `integrations/`, `state/`, and `tmp/` directly under
`TAG_HOME`. The new CLI does not read those paths as the default Tag.
Stop the old Slack bridge and MFS process, back up `TAG_HOME`, then migrate that
data once into `instances/default` before starting the new CLI. Do not merge
live process records or start old and new releases concurrently.

For the alpha layout change, stop Tag and move an existing default workspace
once before installing the updated release:

```sh
mkdir -p "$HOME/Tag"
mv "$HOME/Library/Application Support/Tag/instances/default/workspace" \
  "$HOME/Tag/default"
```

The installer does not merge or remove old workspace directories.

Configuration is JSON on every platform and is never executed as shell code.
POSIX installations create private directories; Windows uses the account's ACL.
MFS retains its own existing data and authentication locations, as do external
tools such as `gws`. The installer does not relocate their credentials.

## Install from a checkout

Install Python 3.10+ and your chosen agent CLI. Tag prefers
[uv](https://docs.astral.sh/uv/) when it is already available and otherwise uses
Python's standard `venv` and pip. Authenticate the agent CLI separately. No local
administrator privileges are needed. Slack installation is separate: a workspace
owner or Enterprise policy may require an app manager to approve the custom Slack
app.

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
The source launcher only uses that checkout's `.venv`; it never falls back to a
system Python and `tag start` never installs packages. For normal use, prefer the
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
