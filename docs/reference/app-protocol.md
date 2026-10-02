# Tag app protocol

Tag.app and other graphical clients do no Tag work themselves. They run the
`tag` command with `--json` and read its output. This page lists the commands a
client relies on. Example payloads live in `protocol/examples/`; the
CLI and app tests both check against them, so a change that breaks a client
fails CI.

Every JSON object has `schema_version: 1`. Clients ignore fields they don't
know. Adding fields is compatible; removing or changing one is not.

## Compatibility

`tag version --json` reports `version`, `app_protocol`, `capabilities`,
`platform`, and `runtime`. `app_protocol` is bumped only for incompatible
changes. A client checks that it supports `app_protocol` and that each feature
it shows is listed in `capabilities`; otherwise it offers to upgrade Tag.

| Capability | Commands |
| --- | --- |
| `list` | `tag list --json` |
| `setup-jsonl` | `tag setup --json`, `tag add --json`, `tag NAME setup --json` |
| `setup-back` | `{"back": true}` during setup |
| `rename` | `tag NAME rename "Name" --json` |
| `workspace-lifecycle` | `tag start\|stop\|restart --workspace TEAM --json` |
| `autostart` | `tag autostart [status\|on\|off] --json`, `keep_running` in `tag list` |
| `autostart-keep` | `tag autostart keep TAG... --json`: keep Tags running without starting them now |
| `logs-json` | `tag NAME logs --json [--limit N]` |
| `upgrade-json` | `tag upgrade --dry-run --json`, `tag upgrade --json` |
| `install-progress` | `TAG_INSTALL_PROGRESS=jsonl` for `install.sh` and `install.ps1` |

## Tags

`tag list --json` returns `tags`, one object per Tag with `id`, `valid`,
`state`, `slack_workspace`, `workspace_name`, `slack_name`, `nickname`,
`avatar`, `keep_running`, and `main`. `state` is `running`, `stopped`,
`not_configured`, `setup_incomplete`, `needs_attention`,
`invalid_configuration`, or `invalid_tag`; treat unknown states as needing
attention.

Start or stop one Tag with `tag NAME start` or `tag NAME stop`; the exit code
is the result. Starting records that the Tag should keep running; stopping
records that it should stay off (see [Keeping Tags running](../tag-management.md#keeping-tags-running)).

## Setup

See [Guided setup over JSON lines](../tag-management.md#guided-setup-over-json-lines).

## Installing

A client ships a copy of `install.sh` (or `install.ps1` on Windows) and
`scripts/tag_install.py` from the same commit, and passes `--channel` or
`--version` explicitly so the bundled, reviewed installer is used rather than
one downloaded at install time. The installer still verifies each release's
checksum and provenance.

With `TAG_INSTALL_PROGRESS=jsonl`, the installer writes lines beginning
`@tag-progress ` followed by a JSON object to stderr, alongside its normal
output. `step` is, in order: `tools`, `python`, `download`, `release` (with
`version`), `components`, `memory`, `command`, and finally `done` (with
`version` and `command`, the installed `tag` path) or `failed` (with
`message`). Steps that aren't needed, such as `tools` when they are already
present, are skipped. A client should use `command` from `done` to run Tag
afterwards rather than relying on `PATH`.

## Upgrading

`tag upgrade --dry-run --json` checks for an update without changing
anything; `status` is `current`, `available`, `pinned`, or `ahead`.
`tag upgrade --json` installs it and restarts running Tags; `status` is
`upgraded` when it did.
