---
name: hover-tag-setup
description: Help install Tag, connect it to Slack through guided setup, resume incomplete onboarding, and verify the first Slack task. Use when someone asks to set up their Tag with an already authenticated Codex CLI.
---

# Hover Tag Setup

Guide the user from an existing Codex installation to a working Tag in Slack.
Use Tag's installer and installed `tag` CLI for all setup and service operations.
This skill contains no runtime implementation. Do not launch bridge Python
scripts directly or manually build an MFS connector for initial onboarding.

## Talk like a person

Most people using this skill are not developers. The rules below are for you;
what you say to the user should be plain and short.

- Keep the number of messages low. Setup already takes many turns, so do
  everything you can without the user, and stop only when you need them. Put
  every step they must do in one place, such as the Slack sign-in, together in
  one numbered message, and ask all questions you already know you need in one
  go. Say why in a few words.
- Describe outcomes, not commands. Say "I'll install Tag now", not "running
  `install.sh`". Mention a command only if the user asks or must run it.
- Write like a helpful coworker: short sentences, no headings or bullet walls
  in chat unless listing choices, no filler like "Great question" or "Let's
  dive in".
- Translate internal terms. Never show these as written:

| Internal term | Say instead |
| --- | --- |
| invitation-following memory | Tag will also read channels you add it to later |
| selected-channel policy | Tag only reads the channels you picked |
| authorization ticket, `/slackauthticket` | a one-time sign-in line from Slack |
| Agent messaging, legacy Assistant experience | Slack's chat feature for the app |
| App ID | the app's ID, shown in Slack's app settings |
| MFS, index sync, App Server | Tag's search of your messages (only if asked) |
| `first_reply: not_verified` | I haven't seen Tag answer in Slack yet |

- When something fails, say what happened, what it means for them, and the one
  next step. Keep stack traces and raw logs out of chat.
- When setup is blocked, lead with the outcome in plain words: nothing was
  changed, what broke in one sentence, and who can fix it. Say whether it's
  something the user can do or a Tag bug to report. Don't name internal modules
  or files unless the user asks.
- If the user is stuck or it looks like a Tag bug, point them to the developers:
  the [Slack community](https://join.slack.com/t/hover-community/shared_invite/zt-4aghkshid-n7fRukS7_J5sR2jDLBXK9A)
  for quick help, or a [GitHub issue](https://github.com/klovr-co/hover-tag/issues)
  for bugs. Offer to draft the issue with redacted details. If the Slack link
  has expired, use the community link on https://hover.team/tag instead.
- Report "Tag is running" and "Tag answered in Slack" as two separate facts, in
  everyday words.

## Inspect before installing

The primary path is macOS/Linux with Codex CLI already installed, signed in,
and able to run tasks on the host computer. Treat Codex as a prerequisite;
help with its installation or sign-in only if missing or requested. Tag runs
on this computer, which must stay awake and connected while handling requests.

Check for Python 3.10+, `curl`, Slack CLI, and Codex. Git is needed only for
a source checkout. Tag prefers `uv` when
available and otherwise uses venv/pip. Slack CLI is installed separately.
The user needs permission to install a Slack app in their chosen workspace;
workspace policy may require administrator approval.

Check whether `tag` is already on PATH and belongs to this product, using its
help and version output. If installed, run:

```sh
tag inspect --json
tag paths --json
```

Use `tag inspect --offline --json` when only local inspection is appropriate.
Read configuration fields, backend, services, state, and next command. Saved
redacted values such as `[set]` are present, not missing. Offline service values
are unknown, not healthy. Do not reinstall or restart a working deployment
merely because the user asks for setup help. Preserve the user's `TAG_HOME`.

## Install when absent

Use the official hosted installer at https://hover.team/tag/install. This URL
serves a shell script directly, not an HTML installation guide. On macOS/Linux,
download it into a fresh temporary directory, inspect the script, and run it
only after the download succeeds:

```sh
tag_install_dir=$(mktemp -d)
curl --proto '=https' --tlsv1.2 -fLsS https://hover.team/tag/install -o "$tag_install_dir/install.sh"
# Inspect the downloaded script before executing the next command.
sh "$tag_install_dir/install.sh"
```

Run download and execution as separate tool calls so a failed download stops
the flow. Remove the downloaded file and its temporary directory afterward.
Let the installer select its configured release channel; do not hard-code a
release version or switch to edge unless requested. If downloading or release
verification fails, report the failure rather than silently installing source.

Use a checkout only when the user requests a source installation or selects an
existing checkout. For an existing checkout, use the user's selected directory
and revision, check for local changes first because they become part of a source
installation, and run that checkout's `./install.sh`. For a new source
installation, clone https://github.com/klovr-co/hover-tag.git into a new,
nonconflicting directory and run its `./install.sh`. Do not overwrite an
existing directory.

The installer creates a persistent home, workspace, and managed runtime.
Use the launcher path it prints for subsequent commands. The default POSIX
command directory is `~/.local/bin`; if PATH omits it, use the absolute launcher
or add that directory to the current shell's PATH. Persist shell-profile changes
only when needed for the requested setup. Use installed `tag`, not the checkout's
`./tag`, after installation. `tag paths --json` provides the actual locations;
normal installations keep default configuration in `~/Tag/default/.tag/config/settings.json`,
not a checkout `.env`.

For Windows requests, use the checkout's `install.ps1` and platform instructions
in `docs/reference/installation-details.md`; do not adapt POSIX shell commands blindly.

## Agree on the setup once

Inspect the installation and existing Slack CLI authorizations before asking
questions. Then present one compact proposed setup using discovered values and
recommended defaults. Include every choice or approval setup is likely to need:

- install or reuse the detected Tag installation;
- Slack workspace, and whether to create a new app or link an App ID;
- suggested app name and picture choice;
- authorized caller, channel or invitation-following policy, and history window;
- Codex or the requested backend; and
- permission to perform the described app creation or linking, installation,
  indexing, and service startup.

Write the proposal for someone who has never seen Tag. Start with what you
found and what you're about to do, in one or two sentences, such as "Tag is
already installed on this computer and connected to your klovr-co Slack. I'll
keep that and finish setup." Then list each choice in everyday words with the
value you'd use. Avoid "authorized caller", "indexing", "preserving data" and
similar; say "only you can ask Tag to do things" and "Tag will read the last 30
days of messages so it has context". If the choice is about to reuse an
existing setup, never offer an alternative like a different workspace or a
fresh setup unless you say what it means and what would be lost. Spell out any
workspace name you mention. Ask the user to reply **Use these defaults** or
list all changes in one message.
Do not ask separately for values that inspection can discover. Treat that reply
as the answer to matching later setup prompts, but do not broaden it to new
actions or unexpected permission changes. Ask again only for an unavoidable
just-in-time Slack approval, a genuinely missing choice, or a new condition
that changes the agreed plan.

## Drive setup one question at a time

Run setup with `--step` and answer its questions from the agreed plan. Do not
tell the user to open Terminal, copy terminal output, or answer numbered
prompts. Setup keeps running in the background between your commands, so it
is fine for a question to wait while you talk with the user.

```sh
tag setup --step                                  # start, or show the current question
tag setup --answer '0' --question workspace       # answer it; prints the next question
tag setup --stop                                  # pause; progress is saved
```

Use `tag add --step` for another workspace. Each command prints one JSON
object. `state` is `waiting` (a `question` needs an answer), `working` (setup is
busy; run `--step` again), or `ended` (see `result`). `events` holds what
happened since your last command. `message` events are progress text: relay
only what matters, in plain words.

Every question has a stable `id`, such as `workspace`, `slack_app`,
`assistant_name`, `history_days`, or `approve_setup`. Map the agreed plan to
these IDs, and always pass `--question <id>` so an answer can't land on the
wrong question. `--answer` takes a JSON value that depends on the question's `kind`:

- `choose`: an index or the exact label from `options`.
- `multi`: a list of indexes or labels; `selected` holds the current picks.
- `text`: a JSON string; `default` is the suggestion.
- `confirm`: `true` or `false`.
- `people`: a member ID from `people`, or `"manual"` to type one instead.
- `slack_login`: see "Connect Slack" below.
- `secret`: never answer with a value from chat. See "Connect Slack".

Answer only questions the agreed plan covers. If an `id` isn't in the plan, or
a question changes the plan, ask the user first while setup waits. A `result`
`status` of `paused` means progress was saved and `--step` resumes it. `failed`
means setup stopped. Report it in plain words with the next step. If nothing
happens for 30 minutes, setup pauses by itself; `--step` picks it up again.

## Connect Slack

If the requested workspace is already in `slack auth list`, setup reuses it.
Otherwise setup asks a `slack_login` question whose `sign_in_line` is a
one-time `/slackauthticket …` line. Never describe "Terminal inside Slack" or
teach the user what an authorization ticket is.

1. Show the user only the `sign_in_line`, and in the same message give short
   numbered steps, for example: "Slack needs to confirm it's really you. This
   takes about a minute.
   1. Open the Slack workspace you want to connect.
   2. Paste the line above into the message box of any channel or DM, then send
      it. It doesn't need to be a Tag channel.
   3. Click **Confirm**.
   4. Slack will show a short code. Copy it and paste it here."
   Do not split those steps into separate turns. Always include Slack's
   illustrated
   [Authorizing the Slack CLI](https://docs.slack.dev/tools/slack-cli/guides/authorizing-the-slack-cli/)
   guide as an optional visual reference.
2. Send the user's code with `--answer '"<code>"' --question slack_login`. Setup finishes the sign-in itself and
   never echoes the code. If it rejects the code, setup says so and asks again
   with the same line. Do not echo the line or code again yourself.

Tell the user the line and code are one-time, short-lived values. Keep them
inside the active exchange. Do not copy them into summaries, diagnostics,
screenshots, issue trackers, or logs.

A `secret` question asks for a Slack token, which is only a recovery path.
Never ask for tokens in chat. Pause the session and have the user finish that
step with `tag setup` in their own terminal, where the token prompt is hidden,
then resume with `--step`.

Setup owns these steps:

- Slack CLI authorization, followed by approved app creation or linking an
  existing app by App ID. An App ID is not a token.
- App compatibility checks and automatic credential handoff. Hidden terminal
  token entry is a recovery option, not the default. Never request tokens in
  chat or put literal credentials in shell arguments, history, or files in this skill.
- A targeted Slack CLI repair when Agent messaging is missing. Setup preserves
  unrelated manifest settings and asks before replacing the irreversible legacy
  Assistant experience; other compatibility changes remain guided manual steps.
- Owner identity, channels, history window, and memory-policy approval.
  Preserve the caller allowlist; widen access only on explicit user request.
- Finish approval, which starts services and indexing unless `--no-start` is used.

Explain the policy shown by the installed version in one or two plain
sentences, using the table in "Talk like a person". Current new setups include
all channels the bot already joined and default to invitation-following memory:
future invitations can expand the channels eligible for replies and indexing.
Private channels require an invitation. Do not describe this as permanently
restricted to the initially selected channels. Existing setups may retain a
selected-channel policy; preserve it unless the user requests a change.

Setup saves completed answers; rerunning it resumes. Use `tag setup --review`
only when the user wants to revisit choices. `tag setup --no-start` saves choices
without starting services or indexing. `tag setup --test` (not combinable with `--step`) implies `--no-start`
and requires a separate MFS server before you run `tag start`. This is not a
Slack sandbox: approved Slack actions remain real and CLI sign-ins are shared.

To add another Tag, in the same or another Slack workspace, run `tag add`. The
flow selects the workspace first, then continues setup. Tag names each Tag after
its Slack team and app IDs once the app exists, for example
`tag t0abc123-a0xyz789 status`; nobody chooses a name. Commands without a name use
the main Tag. The Slack display name, such as “Maya's Tag,” is separate and may
repeat across Tags.

For compatibility or permission failures, use setup's targeted Agent messaging
repair when offered. Otherwise follow the displayed checklist and have the user
or workspace admin make the required Slack changes. Do not loop on app creation,
automatically broaden scopes, or reset the installation.
When app creation has an uncertain outcome, inspect the saved identity before
attempting creation again. Resume with the existing app when possible.

## Verify and run the first task

After setup, inspect readiness:

```sh
tag doctor --json
tag status --json
```

Successful setup normally starts Tag. If configuration is complete but services
are stopped, use `tag start`, then check status. Tag manages its local MFS service;
do not launch a competing MFS instance. Keep Codex as the backend unless the
user requests another supported backend. Its default transport is App Server;
do not configure legacy `codex exec` transport as part of ordinary onboarding.

Locate the managed workspace with `tag paths --json`. For a simple first task,
have the user mention the actual installed bot in an approved channel and ask
it to reply with a short greeting. No pre-existing project files are needed.
Let the user send the message unless they explicitly authorize you to send it.
Observe the reply, or ask the user to confirm it if Slack is not accessible.

Report service readiness and first-reply verification separately. Executable
presence does not prove Codex sign-in. Inspection does not run a model task,
and `first_reply: not_verified` is not a receipt that changes automatically.
A healthy service or requested index sync does not prove a successful Slack task.
If the reply cannot be observed or confirmed, state that verification is pending.
Close with a short plain summary, for example: “Tag is running on this
computer. I haven't seen it answer in Slack yet. Mention @Tag in a channel it's
in and say hi, then tell me what happens.”

Once the first reply works, invite them once to join the
[Tag community on Slack](https://join.slack.com/t/hover-community/shared_invite/zt-4aghkshid-n7fRukS7_J5sR2jDLBXK9A)
for tips and to talk with the developers. Keep it to one friendly line, and
don't repeat it if they ignore it.

## Recover only what failed

Use structured diagnostics and the reported next action before changing settings.
For bounded logs use `tag logs --limit 50`; review and redact third-party output
before sharing it. Never dump credentials or entire settings files into chat.

For a requested targeted correction, discover keys with `tag config keys --json`,
then use `tag config set KEY VALUE --json` for nonsecret values. Credentials use
hidden settings prompts or a secure stdin pipe with
`tag config set KEY --stdin --json`. Preserve unrelated settings. Configuration
changes apply on the next start; restart a running service when the requested
change needs it, then recheck status. Lifecycle commands do not accept `--json`.

Inspection can exit successfully while setup is incomplete. Status and doctor
can return nonzero with useful JSON describing unhealthy services or failed
checks. Read the report rather than treating every nonzero exit as a tool failure.
Repair invalid configuration JSON without silently replacing it. Do not use
`tag reset`, upgrades, or additional data sources as routine onboarding fixes.

For version-specific details, consult the installed CLI's `--help` and the
matching Tag release's `docs/tag-management.md`, `docs/reference/cli-operations.md`, and `docs/reference/installation-details.md`.
These are product references, not files bundled with this skill. In a checkout,
read them locally; otherwise obtain them from the matching revision in
https://github.com/klovr-co/hover-tag. Keep setup mechanics in Tag rather than copying
them into this skill.
