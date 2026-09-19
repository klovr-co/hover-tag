# Run your first Tag task

Start with one local workspace and one Slack channel. That is enough to check
that Tag can receive a request, run Codex in the right place, and reply in the
right thread. Add more sources after this works.

## The goal

An authorized teammate mentions `@Tag` in Slack. Codex works in the directory
you chose, and Tag replies in the same thread.

## Before you begin

You need:

- macOS or Linux;
- Python 3.10 or later;
- [`uv`](https://docs.astral.sh/uv/);
- `curl`;
- a working Codex CLI login;
- permission to create or install a Slack app; and
- a dedicated workspace directory where Tag may perform tasks.

Codex is the qualified backend for the v0.1 alpha path. Claude is available as
an experimental backend.

## 1. Install Tag

```bash
git clone https://github.com/klovr-co/tag.git
cd tag
./install.sh
```

The installer creates a persistent Tag home and installs the `tag` command.
Run `tag setup` to configure Slack and save settings. See
[setup and management](../tag-management.md) for the full setup flow.

## 2. Connect Slack

Run `tag setup` in a terminal. It guides you through connecting Slack,
creating or linking an app, entering tokens, and selecting channels and the
owner member ID. Approve the displayed channels and history window before
Tag indexes Slack history. Use your installed bot's name when mentioning it.

At first, only the owner member ID can invoke Tag. Add teammates to
`SLACK_ALLOWED_USER_IDS` when you are ready to share it.

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

## 4. Check and start Tag

```bash
./tag doctor
./tag start
./tag status
```

`doctor` checks the backend, Slack credentials, authorized users, MFS service,
and configured retrieval scopes. Fix failed checks before testing a mention.

## 5. Delegate a useful task

In the Slack channel where Tag is present, try a request grounded in the
workspace:

> @Tag read the project documentation, summarize what this project is trying to
> accomplish, and list the three most important open questions. For each point,
> tell me which file supports it.

Check the cited files. If they match the answer, you know Slack accepted the
request, Codex ran in the intended workspace, and Tag returned the result to the
right thread.

## 6. Continue in the thread

Reply in the same thread with:

> @Tag turn that into a one-week action plan with an owner placeholder for each item.

Tag receives the earlier thread messages with the new request, so the follow-up
can build on the shared discussion.

## 7. Add broader context

Once the first loop works, add another source through MFS and include its exact
root in `MFS_ALLOWED_SCOPES`. Setup handles the approved Slack history source;
changing retrieval scopes alone does not index additional sources.

## If something fails

Run:

```bash
./tag status
./tag logs
./tag doctor
```

Then match the first failed check in [Troubleshooting](../troubleshooting.md).

Stop Tag with:

```bash
./tag stop
```
