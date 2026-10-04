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
| `describe` | `tag NAME describe "Description" --json`: changes the Slack app's one-line description and verifies Slack saved it; `""` clears it |
| `abandon-setup` | `tag NAME abandon --json`: moves a Tag that never reached Slack to `abandoned/` under the installation root; refuses a Tag that has a Slack app, a bot token, or a running bridge |
| `remove-tag` | `tag NAME remove --json [--delete-app --confirm-app APP_ID]`: stops the Tag and moves its files to `abandoned/`. The Slack app is kept unless `--delete-app` is given with its exact App ID in `--confirm-app` |
| `workspace-lifecycle` | `tag start\|stop\|restart --workspace TEAM --json` |
| `autostart` | `tag autostart [status\|on\|off] --json`, `keep_running` in `tag list` |
| `autostart-keep` | `tag autostart keep TAG... --json`: keep Tags running without starting them now |
| `logs-json` | `tag NAME logs --json [--limit N]` |
| `upgrade-json` | `tag upgrade --dry-run --json`, `tag upgrade --json` |
| `install-progress` | `TAG_INSTALL_PROGRESS=jsonl` for `install.sh` and `install.ps1` |
| `ai-connections` | Per-Tag AI status and model controls; AI setup questions |
| `shared-ai-connections` | Global `tag settings ai connections\|sign-in\|resume --json`; shared accounts, model-only setup |
| `thinking-level` | `tag NAME settings ai effort LEVEL --json`, `model VALUE --effort LEVEL`; thinking-level fields in `settings ai`, `models`, and `tag list` |
| `logs-activity` | `activity` in `tag NAME logs --json` |
| `activity-details` | `tag NAME logs --activity RUN_ID --json` |
| `telemetry-events` | `tag telemetry record EVENT FIELD=VALUE...`, `available` in `tag telemetry status\|on\|off --json`; see [Usage data](#usage-data) |
| `setup-v2` | Setup order Your Tag → AI → Workspace → Create → Channels; `profile`, `org_workspace`, `existing_app`, and `app_checks` questions; `recap`; creation `progress`; `ready` in the result |

## Tags

`tag list --json` returns `tags`, one object per Tag with `id`, `valid`,
`state`, `slack_workspace`, `workspace_name`, `workspace_icon`, `slack_name`,
`slack_app_id`, `has_app`, `nickname`, `avatar`, `keep_running`, `main`, `description`, `default_model`,
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

`avatar` is the path to the bot's cached Slack profile picture. Setup completion
and startup automatically backfill existing Tags using a versioned, retryable
cache; running Tags check Slack hourly and retry failures after five minutes.
This uses the existing `users:read` permission and works with both Codex and
Claude. The last good picture stays available offline. Until the first successful
sync, `avatar` uses the setup picture, or `null` if none exists. A changed picture
gets a new local filename so apps reload it. `tag list` only reads this cache;
it does not make network requests. Syncing never changes the Slack app's picture.

`tag NAME logs --json` returns `services`, each service's recent log lines,
and `activity`: up to 50 of the Tag's recent Slack requests, newest first.
Add `--activity-channel CHANNEL_ID` to select a Slack channel, and `--hide-errors`
to omit failed requests. These filters apply before the 50-item limit and do not
change stored records or service logs. They require `logs --json` and cannot be
combined with `--activity` or `--follow`. Stopped requests remain visible.
`--activity-limit N` expands the recent window (default 50, maximum 10000),
with `activity_has_more` indicating additional matching retained records.
Tag.app displays chronological history with the latest request at the bottom and
scrolls there when Activity opens. Scrolling up loads older history in batches of
50, preserving the reading position; Load older activity also works by keyboard.
New arrivals follow the bottom only while the user is already there. Show errors
is off by default and keeps its choice while navigating Tags and channels.
Channels open on their own Activity tab. A channel feed combines the workspace's Tags'
matching records, preserving each Tag's identity and detail links. Its separate
Tags in this channel tab still lists current channel membership.
Each item has `at` (an ISO 8601 UTC time: when the request finished, or when
it started while it's still running), `kind` (`replied`, `failed`, `stopped`,
or `working`; treat unknown kinds as finished), `channel` (the Slack channel
ID), `channel_name` (from the channels the Tag remembers, or `null` when
unknown or for a direct message), `dm`, and `run_id`. Items come only from Tag's
own activity records, which it keeps for 30 days. The summary omits prompts,
people, and tool steps. See `protocol/examples/logs.json`.

Successful delivered replies can include `reply_summary`: a cached AI-written,
redacted, plain-text TL;DR of the delivered answer. New summaries use a natural
8–14-word sentence capped at 110 characters; older cached summaries can contain
up to 220 characters. Tag.app prefers it over `reply_preview`, the locally generated
excerpt used while a summary is pending or unavailable. Both fields are exposed
in the CLI's JSON activity feed, so reading either interface never starts a model call.
`reply_summary_status` is `pending`, `ready`, or `unavailable`; Tag.app labels fallback
text as a reply excerpt and shows pending/unavailable state. A pending record older
than 30 minutes is displayed as unavailable (including after an interrupted worker).
Optional `backend`, `model`, and `model_name` record the backend's actual reported
model at execution time, for both Codex and Claude. Older records without model
metadata do not inherit the Tag's current setting.
Optional `reasoning_effort` snapshots the thinking level configured for each
request; a supported level reported by the backend takes precedence. This works
for Codex and Claude and is shown beside the model in Tag.app. Older entries
without a saved level omit it rather than inheriting current settings.
`duration_seconds` is elapsed backend request time, including tool work and retries,
computed from retained start/finish timestamps. `usage` holds reported cumulative
`input_tokens`, `output_tokens`, and `total_tokens`, with optional cache/reasoning
subsets (`cache_read_input_tokens`, `cache_creation_input_tokens`, and
`reasoning_output_tokens`). Cache counts are included in input and reasoning
counts in output, so they are not added again to the total. The separate summary
pass is excluded. Tag.app shows the total beside the model; hovering or focusing
the token count opens the reported breakdown. Missing optional counts are omitted.
Tag.app wraps the full summary. A small chevron beside the reply's channel opens
the saved details; its accessible label and tooltip explain the control. Missing token usage and missing, zero, or invalid durations are omitted from the feed.
Finished reply rows without a saved summary, excerpt, or artifact are omitted from
the feed. The compact Show errors control sits beside the Activity tabs; empty
feeds simply say there is no activity. This is a presentation filter; CLI activity
JSON retains all records for inspection. Both fields are available through
CLI activity JSON. These additive fields require no configuration migration.

New requests also retain optional `artifacts`, with up to 20 output records:
`name`, `kind` (`file` or `image`), and `delivery` (`uploaded`, `local`, or
`upload_failed`). Validated workspace outputs can include `local_path`; generated
images never expose their temporary path. Uploaded files can include a permanent
Slack file `url`, with query strings and fragments removed. `artifact_thread_url`
opens the original Slack thread when an upload succeeded without a file permalink.
Tag.app renders compact filename chips below the summary, opening uploaded files
in Slack and saved workspace files locally. Local-only and failed uploads are
labeled; failed temporary images have no open action. The same metadata is
available in CLI activity JSON. Older records omit it, without speculative
backfilling or a stored-data migration.

After Slack delivery, Tag queues a separate restricted summary run using the
request's backend and model with its existing connected account. Codex and Claude
both support this automatically, including replies delivered through legacy
transports. It consumes one additional AI turn per new reply and does not delay
Slack delivery. The run receives only redacted reply text, bounded to 32,000
characters (retaining the beginning and ending of longer replies), with task
tools and user hooks disabled and a 45-second deadline. Summaries retain material
limitations and unfinished work rather than inventing successful outcomes.
The summary starts after generated-image uploads finish and receives bounded
artifact names, kinds, and actual delivery states, without local paths or URLs.
Recorded delivery status takes precedence over a reply's claim that a file was
attached. File chips carry the filenames so the sentence can stay short. This
shared delivery and summary flow applies to both Codex and Claude.

One background worker handles a bounded queue of 32 waiting replies. Duplicate
jobs are coalesced and successfully saved summaries are never regenerated. On
provider failure, timeout, shutdown, or queue saturation the excerpt remains.
Full replies and pending jobs are not persisted, so interrupted jobs are not
replayed at startup. The optional fields are compatible with existing records;
no configuration migration or setup is required. Older replies without saved
answer text keep their outcome label and are not reconstructed from tool activity.

Channel names are cached privately by workspace and channel, independently of
memory settings. Startup backfills configured channels and retained activity
for existing installations, reusing saved names before looking up missing ones.
Successful lookups are kept; partial failures retry on the next start, and the
versioned migration checkpoint is written only after verification. Invitation
discovery updates names it already receives, including renames. The cache is
display metadata, never a membership or authorization source. `tag list` and
`logs --json` read it without contacting Slack.

`tag NAME logs --activity RUN_ID --json` returns `ok` and `activity` with the
run's `outcome`, `started_at`, `finished_at`, Slack `team`, `channel`, `thread_ts`,
bounded `events`, `omitted` count, and nullable `error` (`reference` and sanitized
report `text`). Each event includes its public `label`, `status`, timestamps,
and redacted `details` (`tool`, `input`, `output`, when recorded). The command
also works without `--json` for terminal inspection. Missing, invalid, or expired
runs return exit code 1 with an `error` in JSON mode. Reads do not change records.

Tag.app loads these details on demand and reuses finished results while the feed
is open. Both Codex App Server and Claude Agent SDK use the same stored event
contract. Older records work without a rewrite; missing input and result previews are omitted. Error reports match the exact saved reference and request routing;
older runs without a reference show a report only when routing and the run's time
window give one unambiguous match. Prompts and private reasoning are not added.

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
of the PNG, JPEG, or GIF Tag will upload), `preview_revision` (SHA-256 of the
image bytes), `picture` (`waterdrop` or `custom`),
`picture_label` (such as `Water · Tag waterdrop #0042`, or the file name),
`error` (why the last answer didn't work, or `null`), `editing`, and
`can_use_existing`. Answers:

- `"shuffle"`: a new waterdrop from any of the five elements, differing in
  element or signature, that no other Tag on this computer uses. The question
  is asked again with the new `preview_revision`. The `preview` path can stay
  the same: clients must include the revision in the displayed image URL to
  invalidate cached pixels. The Create recap supplies `picture_revision` for
  the same purpose. Older saved pictures get their revision automatically on
  read. Shuffles don't count for Back.
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

`workspace.icon` and `owner.icon` are optional local image paths. Setup caches
them using an already connected Tag in the selected workspace, including on
resumed setups from older installations. No extra authorization is requested.
Slack CLI sign-in alone cannot supply pictures before the first app is installed;
missing permissions, unavailable images, or a default workspace icon produce
the existing initial/person fallback. Images are cosmetic and never block
creation. The CLI emits the same fields in JSON; its text summary stays textual.

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
model. Settings manages one shared connection per provider for all Tags; Tag
details and setup select models. `tag settings ai connections --json` works
before creating a Tag and returns `schema_version`, `scope: "installation"`,
`connections`, `usable`, and `running` (whether any Tag is running).

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
true for every provider connection, including an explicitly connected ChatGPT plan.

`tag NAME settings ai models --json` lists the connected accounts' models in
`groups`, one per backend, each starting with the account's own default (the
bare backend value, such as `codex`). Each model has `efforts`, the thinking
levels it offers (`[]` for a model without any), and `default_effort`, the
level it uses unless the Tag picks one, or `null`; the account default shows
its default model's levels. `default.available` is false when the
saved default is no longer offered; `suggested` is the model to preselect.
Loading models can take several seconds.

`tag NAME settings ai model VALUE --json` saves the default model; the backend
follows the model. All Slack requests use the Tag's settings; per-user model,
thinking, and Fast Mode overrides are retired automatically on startup.
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

`tag settings ai sign-in BACKEND --json` runs the backend's browser
sign-in and writes JSON lines: `progress` events with `step` (`stopping`,
`browser`, `waiting`, `verifying`, `restarting`), `text`, and sometimes `url`
to reopen the sign-in page, then one `sign_in` event with `status` `connected`,
`cancelled`, or `failed`, plus `connection`, `error`, and `retry`. Send
`{"cancel": true}` or close stdin to cancel; run the command again to retry.
For Codex, `--method chatgpt` connects one shared ChatGPT account and
`--method codex` uses the computer's Codex sign-in. For both providers, changing
accounts while any Tags are running needs `--restart`: stop those Tags during
sign-in and restore them afterwards, even if sign-in fails. Previously stopped
Tags stay stopped. `tag settings ai resume --restart` resumes a paused plan.
Named-Tag connection commands are rejected. See `protocol/examples/ai-*.json`
and `ai-sign-in.jsonl`.

During `tag setup --json`, the AI step uses `choose` questions. When an agent is
connected, `default_model` has model-only `option_ids`, `groups`, `connections`,
and `tag_name`. Runtimes advertising `supports_effort: true` also accept
`{"answer":{"value":"codex:gpt-5.5","effort":"high"}}` to save model and thinking
together. `default_effort` carries the saved level; each model's `efforts` and
`default_effort` describe its supported levels and default. `effort: "default"`
clears the override. Unsupported levels are rejected before saving either choice.
CLI setup asks for thinking after the model. Older clients can still answer with
just the model, keeping a compatible saved level. When nothing usable is connected, `ai_connection` offers only
`check` and points to global Settings or `tag settings ai sign-in` in another
terminal. The app pauses setup, opens Settings, and resumes setup when you return.
Setup questions cannot sign in or change accounts. Answer with an option ID,
index, or label.

## Usage data

`tag telemetry status --json` reports `enabled`, `available` (whether this
build can collect), `saved_preference` (`on`, `off`, or `not_set`),
`process_override` (`off` when `TAG_TELEMETRY=off` is set), and
`privacy_notice`. `tag telemetry on --json` and `tag telemetry off --json`
save the installation-wide choice and print the same object. With `--json`,
`on` doesn't print the terminal notice, so a client must show the notice from
[telemetry](telemetry.md) itself before turning collection on. Show it while
`saved_preference` is `not_set`, `available` is true, `privacy_notice` is set,
and `process_override` is `null`.

With `telemetry-events`, `tag telemetry record EVENT FIELD=VALUE... --json`
queues one of the fixed `app_` events listed in [telemetry](telemetry.md) when
the saved preference is on, and does nothing otherwise. Every field named for
the event is required and no others are accepted; durations are
`elapsed_seconds` as whole seconds. An unknown event or field exits 2. Values
outside an event's closed lists are dropped without an error.

A client sets `TAG_TELEMETRY_SOURCE=app` when it runs `tag`, so Tag doesn't
record that run as command or setup usage of its own.

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
