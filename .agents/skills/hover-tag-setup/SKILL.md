---
name: hover-tag-setup
description: Help install Tag, connect it to Slack through guided setup, resume incomplete onboarding, and verify the first Slack task. Use when someone asks to set up their Tag with an already authenticated Codex CLI.
---

# Hover Tag Setup

Guide the user from an existing Codex installation to a working Tag in Slack.
Use Tag's installer and installed `tag` CLI for all setup and service operations.
This skill contains no runtime implementation. Do not launch bridge Python
scripts directly or manually build an MFS connector for initial onboarding.

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
in `docs/installation.md`; do not adapt POSIX shell commands blindly.

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
  indexing, and service startup. If Slack authorization is needed, include the
  handoff choice: show the one-time connection in chat (default), or use the
  local clipboard to keep the one-time values out of chat.

Ask the user to reply **Use these defaults** or list all changes in one message.
Do not ask separately for values that inspection can discover. Treat that reply
as the answer to matching later setup prompts, but do not broaden it to new
actions or unexpected permission changes. Ask again only for an unavoidable
just-in-time Slack approval, a genuinely missing choice, or a new condition
that changes the agreed plan.

Drive `tag setup` yourself in an interactive tool session and answer its prompts
from the agreed plan. Do not tell the user to open Terminal, copy terminal
output, or answer setup's numbered prompts. Do not pipe guessed answers; keep the
session interactive and pause if a prompt is not covered by the plan.

## Connect Slack without exposing the CLI flow

If the requested workspace is already present in `slack auth list`, select it
and skip authorization. Otherwise, keep the Slack CLI mechanics behind the
agent. Never describe “Terminal inside Slack” or teach the user what an
authorization ticket is.

### Visible handoff in chat

Use this by default unless the user selected the private clipboard option:

1. Run `slack auth login --no-prompt`, retaining its one-time ticket for the
   completion command. Show only the complete `/slackauthticket …` line to the
   user; do not dump the surrounding CLI output.
2. In the same message, say: “In the Slack workspace you want to connect, paste
   the line above into the message box of **any channel or DM** and send it. It
   does not need to be a Tag channel. Choose **Confirm**, then copy the short
   code from the next Slack window and send that code back here.” Do not split
   those actions into separate turns.
3. When the user replies with the code, complete the exchange with `slack auth
   login --ticket <ticket> --challenge <code>`. Do not echo either value again.
4. Verify the resulting workspace with `slack auth list`, then continue the
   agreed setup without asking the user to repeat prior choices.

Tell the user that the displayed command and returned code are one-time,
short-lived connection values. Keep them confined to the active setup exchange;
do not copy them into summaries, diagnostics, screenshots, issue trackers, or
persistent application logs.

Always include Slack's illustrated
[Authorizing the Slack CLI](https://docs.slack.dev/tools/slack-cli/guides/authorizing-the-slack-cli/)
guide in the first handoff message, whether using chat or the private clipboard.
Present it as an optional visual reference, not a required setup step.

### Private clipboard handoff

Use this only when the user selects it and a supported local clipboard is
available. Explain in the initial proposal that it temporarily replaces the
clipboard with a one-time Slack connection command and later reads the short
code the user copies from Slack. After approval:

1. Run `scripts/slack_auth_clipboard.py begin`. Do not print or repeat the
   command it places on the clipboard.
2. Tell the user: “In the Slack workspace you want to connect, paste into the
   message box of **any channel or DM** and send. It does not need to be a Tag
   channel. Choose **Confirm**, copy the short code from the next Slack window,
   then reply **copied** here.” This is one user turn; do not split it into
   separate checks.
3. After the user replies, run `scripts/slack_auth_clipboard.py complete --state
   <state_file>` using the state path returned by `begin`.
4. Verify the resulting workspace with `slack auth list`, then continue the
   already-running setup plan without asking the user to repeat prior choices.

The helper keeps the one-time command and short code out of agent chat and tool
output. Never inspect, print, summarize, or ask the user to send either value.
If clipboard access becomes unavailable, offer the visible chat handoff instead
of reverting to a terminal tutorial. On a failed or expired exchange, discard
the saved state and begin once with a fresh command rather than retrying the old
values.

Run the installed `tag setup` in an interactive tool session. Ask the user before
answering authorization or policy prompts when their choice is not already
explicit. Do not invent a noninteractive setup API.

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

Explain the policy shown by the installed version. Current new setups include
all channels the bot already joined and default to invitation-following memory:
future invitations can expand the channels eligible for replies and indexing.
Private channels require an invitation. Do not describe this as permanently
restricted to the initially selected channels. Existing setups may retain a
selected-channel policy; preserve it unless the user requests a change.

Setup saves completed answers; rerunning it resumes. Use `tag setup --review`
only when the user wants to revisit choices. `tag setup --no-start` saves choices
without starting services or indexing. `tag setup --test` implies `--no-start`
and requires a separate MFS server before you run `tag start`. This is not a
Slack sandbox: approved Slack actions remain real and CLI sign-ins are shared.

To connect an additional Slack workspace, run `tag add`. The flow selects the
workspace first, suggests a lowercase local workspace alias from its real Slack
name, and then continues setup. The alias appears in commands such as
`tag klovr status`; it is separate from the assistant display name, so multiple
workspaces may all use a Slack name such as “Maya's Tag.”

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
matching Tag release's `docs/tag-management.md` and `docs/installation.md`.
These are product references, not files bundled with this skill. In a checkout,
read them locally; otherwise obtain them from the matching revision in
https://github.com/klovr-co/hover-tag. Keep setup mechanics in Tag rather than copying
them into this skill.
