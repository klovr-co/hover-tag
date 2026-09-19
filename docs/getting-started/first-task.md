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

The installer checks MFS, creates a private `.env`, and asks for the Slack and
workspace settings it cannot guess.

## 2. Connect Slack

When prompted, create a Slack app from
[`slack-app-manifest.yaml`](../../slack-app-manifest.yaml):

1. Open **Slack API → Your Apps → Create New App → From an app manifest**.
2. Install the app to your workspace.
3. Create an app-level token beginning with `xapp-` and grant
   `connections:write`.
4. Give the installer the app token, bot token, and your Slack member ID.
5. Invite the bot to the channel where you will test it.

At first, only the owner member ID can invoke Tag. Add teammates to
`SLACK_ALLOWED_USER_IDS` when you are ready to share it.

For every scope and token detail, use the
[Slack adapter reference](../../references/slack-adapter.md).

## 3. Choose the working workspace

Set `OPENTAG_WORKDIR` to the directory where the agent should perform tasks.
For a first run, use a dedicated test project whose contents you understand.

The workspace is different from MFS memory:

- the workspace is where the agent can inspect, run, and change things;
- MFS is how the agent retrieves context from approved indexed sources.

The installer adds the selected workspace as a local MFS source. Starting here
makes a bad path or retrieval result easy to spot.

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

> Turn that into a one-week action plan with an owner placeholder for each item.

Tag receives the earlier thread messages with the new request, so the follow-up
can build on the shared discussion.

## 7. Add broader context

Once the first loop works, add Slack history or another source through MFS and
include its exact root in `MFS_ALLOWED_SCOPES`. Tag consumes sources that are
already indexed; it does not connect new organizational systems silently.

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
