# Run your first Tag task

Start with one local workspace and one Slack channel. That is enough to check
that Tag can receive a request, run Codex in the right place, and reply in the
right thread. Add more sources after this works.

## The goal

You mention your Tag in Slack. Codex works in its configured workspace, and
Tag replies in the same thread, where your teammates can follow the work.

## Before you begin

Use a Mac or Linux computer that can stay awake and connected to the internet
while Tag handles requests. Before starting, have these ready on that computer:

- [Codex CLI](https://learn.chatgpt.com/docs/codex/cli), installed, signed in, and able to run tasks.
- [Python 3.10 or later](https://www.python.org/downloads/).
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/), which installs Tag's Python dependencies.
- [`curl`](https://curl.se/download.html), to download Tag's installer.
- [Slack CLI](https://docs.slack.dev/tools/slack-cli/), which connects setup to your Slack workspace.

You'll also need permission to create and install a Slack app in your workspace.
Your workplace may require an administrator to approve the app.

This guide uses Codex. Claude support is coming soon.

## 1. Install Tag

```bash
curl -fsSL https://hover.team/tag/install | sh
```

The installer creates a persistent Tag home, including a workspace folder for
your files, and installs the `tag` command. It prints the command's location.
If your terminal cannot find `tag`, add the default command directory to this
terminal's path:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Add the same line to your shell configuration (`~/.zshrc` for zsh or
`~/.bashrc` for bash) to keep it available in new terminals.

Run `tag setup` to configure Slack and save settings. See
[setup and management](../tag-management.md) for the full setup flow.

## 2. Connect Slack

Run `tag setup` in a terminal. It guides you through connecting Slack,
creating or linking an app, entering tokens, and selecting channels and the
owner member ID. Approve the displayed channels and history window before
Tag indexes Slack history. Use your installed bot's name when mentioning it.

Use your own Slack member ID as the owner. Only you can request
work from your Tag. This matters because the agent runs on the host computer
with the file access, tools, and connected accounts available to its backend.
Actual access depends on backend permissions, the local account, and
credentials. Other channel members can still see your requests and Tag's replies.

Each person brings their own Tag through a separate Slack app. Multi-user
access is coming soon. See [Your own Tag](../concepts/access.md) for how
personal agents fit into shared conversations.

For every scope and token detail, use the
[Slack adapter reference](../../references/slack-adapter.md).

## 3. Choose the working workspace

Tag manages a workspace under its persistent home. Use `tag inspect --json`
to inspect the current configuration, and put the project files you want
Tag to work on in that workspace.

The workspace is different from MFS memory:

- the workspace is where the agent can inspect, run, and change things;
- MFS is how the agent retrieves context from approved indexed sources.

Workspace files are available to the agent directly. Searchable context
depends on which sources are indexed and permitted through MFS.

The workspace folder is the agent's starting directory, not a security sandbox.

## 4. Check and start Tag

```bash
tag doctor
tag start
tag status
```

`doctor` checks the backend, Slack credentials, authorized users, MFS service,
and configured retrieval scopes. Fix failed checks before testing a mention.

## 5. Delegate a useful task

In the Slack channel where Tag is present, try a request grounded in the
workspace. These examples use Maya's Tag; select your own Tag's mention in Slack:

> @Maya's Tag read the project documentation, summarize what this project is trying to
> accomplish, and list the three most important open questions. For each point,
> tell me which file supports it.

Check the cited files. If they match the answer, you know Slack accepted the
request, Codex ran in the intended workspace, and Tag returned the result to the
right thread.

## 6. Continue in the thread

Reply in the same thread with:

> @Maya's Tag turn that into a one-week action plan with an owner placeholder for each item.

Tag receives the earlier thread messages with the new request, so the follow-up
can build on the shared discussion.

## 7. Add broader context

Once the first loop works, make your existing tools and connected accounts
available to Tag. See [Adding integrations](../concepts/adding-integrations.md).
Availability depends on the local account and environment running Codex.

Setup handles approved Slack history indexing. For Slack requests, the bundled
MFS helpers are currently filtered to the invoking channel's Slack scopes;
adding another source to `MFS_ALLOWED_SCOPES` does not make it available through
those helpers.

## If something fails

Run:

```bash
tag status
tag logs
tag doctor
```

Then match the first failed check in [Troubleshooting](../troubleshooting.md).

Stop Tag with:

```bash
tag stop
```
