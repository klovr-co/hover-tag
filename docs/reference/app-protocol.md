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
| `setup-v2` | Setup order Your Tag → AI → Workspace → Create → Channels; `profile`, `org_workspace`, `existing_app`, and `app_checks` questions; `recap`; creation `progress`; `ready` in the result |

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

`tag setup --json` and `tag add --json` run guided setup over JSON lines; see
[Guided setup over JSON lines](../tag-management.md#guided-setup-over-json-lines)
for the event types, answers, Back, and `--step`. With `setup-v2`, both the
terminal and the JSON-lines setup follow the same order and use the same words.
`protocol/examples/setup.jsonl` is a whole session.

**New app:** Your Tag (`profile`) → AI (`ai_connection` only when nothing is
connected, then `default_model`) → Workspace (`workspace`, plus `slack_login`
only when needed or chosen, plus `org_workspace` for an organization) → Create
(`approve_setup`, then creation `progress`) → Channels (`channels`) → `result`.

**Existing app:** `profile` answered with `"existing"` → AI → Workspace → Your
app (`existing_app`, `app_checks`) → Channels → `result`.

Nothing changes in Slack before the Workspace step. A paused setup resumes at
the first step still missing: with no Slack workspace yet it starts at `profile`
with the saved name, description, and picture; after choosing a workspace it
continues at `approve_setup` (or `existing_app`). Setups paused in an earlier
release's order resume without repeating finished steps.

### Your Tag: `profile`

`kind` is `profile_picture`. Fields: `name` (the saved name, or the computer
account's first name, such as `Maya's Tag`), `name_limit` (35), `description`
(one line, may be empty), `description_limit` (140), `preview` (absolute path
of the PNG, JPEG, or GIF Tag will upload), `picture` (`waterdrop` or `custom`),
`picture_label` (such as `Water · Tag waterdrop #0042`, or the file name),
`error` (why the last answer didn't work, or `null`), `editing`, and
`can_use_existing`. Answers:

- `"shuffle"`: a new waterdrop from any of the five elements, differing in
  element or signature, that no other Tag on this computer uses. The question
  is asked again with the new `preview`. Shuffles don't count for Back.
- `{"picture": "/path/to/file"}`: a PNG, JPEG, or GIF from 512 to 2000 pixels on
  each side (Slack CLI 4.7 or newer). Tag copies it and asks again with the copy
  as `preview`, or with `error`, such as `This picture is 300×300. Use one
  between 512×512 and 2000×2000 pixels.` or `Use a PNG, JPEG, or GIF image.`
- `{"name": "…", "description": "…"}`: saves `OPENTAG_BOT_NAME` and
  `OPENTAG_BOT_DESCRIPTION` and continues, or asks again with `error`.
- `"existing"`: use an app you already have (only when `can_use_existing`).

The first picture is seeded by `{tag_id}:{name}`, never by the Slack workspace.
The description becomes the app's `display_information.description` and
`agent_view.agent_description` when Tag creates the app; without one, Slack
shows Tag's default text. Tags created earlier keep their text.

### Workspace: `workspace`, `org_workspace`, `org_workspace_id`

`workspace` is a `choose` question listing the Slack CLI's sign-ins
(`slack auth list`). `option_ids` are the sign-ins' Team IDs (`T…` for a
workspace, `E…` for an organization), then `sign_in` and `exit`. `workspaces`
has `id`, `name`, `kind` (`workspace` or `organization`), `user_id`, and
`user_name` (the Slack handle when the CLI shows one, otherwise `null`).
`sign_in` asks `slack_login`, then `workspace` again. With no sign-ins at all,
setup asks `slack_login` first.

Choosing an organization asks `org_workspace`: `option_ids` are its workspaces'
`T…` IDs and `manual`, `workspaces` lists `{"id", "name"}`, and `organization`
is `{"id", "name"}`. `manual` asks `org_workspace_id`, a `text` question for a
workspace address or ID, which accepts `T…` or an address containing it, such
as `app.slack.com/client/T…`. Its `error` explains an `E…` organization ID or
an answer without an ID.

Tag can't list an organization's workspaces yet, so `workspaces` is empty and
only `manual` is offered. The Slack CLI (4.8) shows that list only inside its
own prompts while it installs or creates an app, and `slack api auth.teams.list
--team E…` runs without the sign-in's token. Calling Slack directly would mean
reading the CLI's private credential file, which Tag doesn't do. Clients should
show the list whenever it isn't empty.

The owner is always the signed-in member: `user_id` for the chosen sign-in, or
Slack's `auth.test` through the Slack CLI's own authorization. If neither says
who it is, setup ends with a `failed` result: "Sign in to Slack again". There is
no people picker and no member-ID entry; the `people` question kind is gone.
Owners already saved on a Tag are kept.

### Create: `approve_setup`

A `choose` question, prompt `Ready to create it in WORKSPACE?`, `option_ids`
`create`, `edit`, `edit_ai`, `back`. `recap` has `name`, `description`,
`picture`, `workspace` (`id`, `name`, `organization` as `{"id", "name"}` or
`null`), `owner` (`id`, `name`), `ai` (`value`, `backend`, `backend_name`,
`label`), and `approval` (true for an organization, whose admin may need to
approve; setup waits and resumes). `edit` asks `profile` with `editing: true`
and `can_use_existing: false`, and `edit_ai` asks `default_model`; both return
here. `back` asks `workspace` again.

`create` makes the app, reported as `{"type": "progress", "step": …, "text": …}`
events without a `backend`: `create` (Create the Slack app), `picture` (Add the
picture), `install` (Install in WORKSPACE, or Add it to WORKSPACE for an
organization), and `connect` (Connect to this Mac).

### Your app: `existing_app`, `app_id`, `app_checks`

`existing_app` is a `choose` question. `apps` lists the apps Tag knows for the
workspace, each with `id`, `name`, `source` (`linked` to this Tag's Slack CLI
project, `cli` from `slack app list`, or `tag`), and `used_by`: the name of
another Tag on this computer that uses it, or `null`. One app serves one Tag,
so used apps aren't in `options`. `option_ids` are the selectable App IDs, then
`other` and `exit`. `other` asks `app_id`, a `text` question for an app address
(`api.slack.com/apps/A…`) or ID; its `error` names the Tag already using a
pasted app.

Next, `app_checks` shows `checks`, each `{"label", "ok", "detail"}` (such as
`{"label": "Missing 2 permissions", "ok": false, "detail": "assistant:write,
channels:join"}`). `option_ids`: `update` (add only Tag's missing settings to
the app, keeping the rest; Slack asks to reinstall it when Tag connects),
`manual` (prints the api.slack.com steps, then checks again), `check`, and
`back`. When everything passes it offers `connect` and `back`. Tag changes the
app only after `update`.

### Channels: `channels`

A `multi` question, prompt `Where should NAME start?`. `options` are joined
channels and public channels Tag can join; `selected` starts with the joined
ones. `channels` has `id`, `name`, `member`, `private`, and `members` (or
`null`). `allow_empty` is true for Tags that follow invitations
(`SLACK_CHANNEL_POLICY=invited`, the default for new setups), so `[]` skips the
step; Tag picks up each channel it's invited to while it runs. Choosing a public
channel Tag isn't in joins it. Memory setup follows without more questions.

### Result

A complete `result` has `ready`: `team`, `app_id`, `channels` (`id`, `name`),
and `ai` (`backend`, `backend_name`, `label`), for Slack links such as
`slack://app?team=T…&id=A…&tab=messages` and `slack://channel?team=T…&id=C…`.
Setup never starts services.

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
