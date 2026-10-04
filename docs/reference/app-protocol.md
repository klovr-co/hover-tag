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
| `ai-connections` | `tag NAME settings ai [models\|sign-in\|resume\|model] --json`; `ai_connection` and `default_model` setup questions |
| `thinking-level` | `tag NAME settings ai effort LEVEL --json`, `model VALUE --effort LEVEL`; thinking-level fields in `settings ai`, `models`, and `tag list` |
| `logs-activity` | `activity` in `tag NAME logs --json` |

## Tags

`tag list --json` returns `tags`, one object per Tag with `id`, `valid`,
`state`, `slack_workspace`, `workspace_name`, `workspace_icon`, `slack_name`,
`nickname`, `avatar`, `keep_running`, `main`, `description`, `default_model`,
`default_model_label`, `default_model_name`, `default_effort`, and `channels`. `state` is
`running`, `stopped`, `not_configured`, `setup_incomplete`, `needs_attention`,
`invalid_configuration`, or `invalid_tag`; treat unknown states as needing
attention.

`description` is the Tag's one-line description (`OPENTAG_BOT_DESCRIPTION`, up
to 140 characters), or `null` when none is saved. `default_model` is the saved
choice (such as `codex:gpt-5.5`, or `codex` for the account's own default),
`default_model_label` names the agent and model (`Codex · GPT-5.5`), and
`default_model_name` just the model (`GPT-5.5`, or `Account default`).
`default_effort` is the thinking level the default model uses (see
[AI connections](#ai-connections)), or `null` when the model has no thinking
levels or Tag hasn't seen its account's catalog yet. These fields come from
saved settings and the last model catalog Tag loaded, so `tag list` never
starts an agent.

`channels` lists the Slack channels the Tag answers and remembers in, each as
`{"id": "C…", "name": "launch"}`, sorted by name. It includes channels the Tag
joined by invitation once Tag has picked them up. `name` comes from the Tag's
saved channel sources and is `null` when Tag hasn't recorded one; apps then show
the ID. Private channels are included once the Tag is invited.

`workspace_icon` is the path to a local copy of the Slack workspace's icon, or
`null` when the workspace uses Slack's default icon or Tag hasn't saved one
yet. Tag refreshes it when setup finishes and on each `tag start`. Show the
workspace name on its own, or a placeholder, when it is `null` or the file
can't be read.

`tag NAME logs --json` returns `services`, each service's recent log lines,
and `activity`: up to 50 of the Tag's recent Slack requests, newest first.
Each item has `at` (an ISO 8601 UTC time: when the request finished, or when
it started while it's still running), `kind` (`replied`, `failed`, `stopped`,
or `working`; treat unknown kinds as finished), `channel` (the Slack channel
ID), `channel_name` (from the channels the Tag remembers, or `null` when
unknown or for a direct message), and `dm`. Items come only from Tag's own
activity records, which it keeps for 30 days; prompts, people, and tool steps
are never included. See `protocol/examples/logs.json`.

Start or stop one Tag with `tag NAME start` or `tag NAME stop`; the exit code
is the result. Starting records that the Tag should keep running; stopping
records that it should stay off (see [Keeping Tags running](../tag-management.md#keeping-tags-running)).

## Setup

See [Guided setup over JSON lines](../tag-management.md#guided-setup-over-json-lines).

## AI connections

A Tag needs at least one connected agent, Codex or Claude, and has one default
model. Both setup and Settings use these commands; the CLI's `tag settings` →
AI & models offers the same choices.

`tag NAME settings ai --json` checks each backend now and returns
`connections`, `usable` (connected backends the Tag may use),
`default_model` (`value`, `backend`, `model`, `label`, `backend_name`,
`available`), and the default model's thinking level: `default_effort` (the
level it uses, or `null`), `effort_levels` (the levels it offers; `[]` when it
has none or Tag hasn't loaded its catalog yet), and `effort_chosen` (true when
the Tag has its own level rather than the model's default). Each connection has `backend`, `name`, `provider`, `state`,
`installed`, `version`, `method`, `account`, `detail`, `shared`, `actions`, and
`install_url`. `state` is `connected`, `signed_out`, `expired`,
`limited` (a paused ChatGPT plan), `not_installed`, or `unsupported`; treat
unknown states as not usable. `actions` lists what to offer: `sign_in`,
`reconnect`, `change_account`, `install`, `resume`, or `update`. `shared` is
true when the sign-in belongs to this computer (Codex or Claude sign-in) and
false for a ChatGPT plan connected to this Tag only.

`tag NAME settings ai models --json` lists the connected accounts' models in
`groups`, one per backend, each starting with the account's own default (the
bare backend value, such as `codex`). Each model has `efforts`, the thinking
levels it offers (`[]` for a model without any), and `default_effort`, the
level it uses unless the Tag picks one, or `null`; the account default shows
its default model's levels. `default.available` is false when the
saved default is no longer offered; `suggested` is the model to preselect.
Loading models can take several seconds.

`tag NAME settings ai model VALUE --json` saves the default model; the backend
follows the model. People's own model choices in Slack are kept.
`restart_required` is true when the Tag is running; add `--restart` to restart
it now, which reports `restarted`. Add `--effort LEVEL` to save a thinking
level in the same change, or `--effort default` for the model's own; the level
must be one the model offers. Without `--effort`, the Tag's level is kept if
the new model offers it and otherwise cleared. The result's `default_effort` is
the level now in effect.

`tag NAME settings ai effort LEVEL --json` saves the Tag's thinking level for
its default model; `effort default` clears it so the model's own default
applies. It returns `ok`, `default_effort`, `restart_required`, and
`restarted`, and accepts `--restart` like `model`. A level the model doesn't
offer is refused. People who chose their own thinking level in Slack keep it.
See `protocol/examples/ai-effort.json`.

`tag NAME settings ai sign-in BACKEND --json` runs the backend's browser
sign-in and writes JSON lines: `progress` events with `step` (`stopping`,
`browser`, `waiting`, `verifying`, `restarting`), `text`, and sometimes `url`
to reopen the sign-in page, then one `sign_in` event with `status` `connected`,
`cancelled`, or `failed`, plus `connection`, `error`, and `retry`. Send
`{"cancel": true}` or close stdin to cancel; run the command again to retry.
For Codex, `--method chatgpt` connects a ChatGPT account to this Tag only and
`--method codex` uses the computer's Codex sign-in. Changing a running Tag's
ChatGPT plan needs `--restart`, which stops the Tag while you sign in and
starts it again afterwards, even if sign-in fails. `resume` resumes a paused
plan the same way. See `protocol/examples/ai-*.json` and `ai-sign-in.jsonl`.

During `tag setup --json`, the AI step uses `choose` questions with extra
fields, so older clients still show plain options. When an agent is connected,
setup asks only `default_model`: `option_ids` are model values, followed by
sign-in options for other agents (such as `sign_in:claude` or
`reconnect:codex`); it also has `groups`, `connections`, and `tag_name`. Only
when nothing usable is connected does it ask `ai_connection` first, with
`option_ids` (such as `sign_in:claude`, `install:codex`, `check`, `exit`),
`connections`, and `can_continue`. After a sign-in, the next question has
`last_result`. A sign-in started from either question writes the same
`progress` and `sign_in` events and accepts `{"cancel": true}`; `result` still
only ever means setup ended. Answer with an option ID, index, or label.

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
`upgraded` when it did. Add `--channel stable|beta|alpha|edge` to either command to preview or
switch the release channel; `current.channel` and `target.channel` report the
saved and requested channels. Switching to a channel whose newest release is
older than the installed one saves the channel and keeps the installed
release (`status` `channel-updated`) until that channel catches up.
