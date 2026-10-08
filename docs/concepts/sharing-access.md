# Sharing access to your Tag

Your Tag starts with you as its only authorized caller: the Slack account you
signed in with during setup. You can deliberately
authorize additional Slack members when you want them to ask your Tag for work.

## What sharing access means

An authorized caller can start work with the same local agent environment that
your Tag uses. That can include its workspace files, installed tools, and
connected accounts, subject to the backend's own permissions. Treat adding a
caller as granting access to ask that environment to act, not merely permission
to read a Slack conversation.

People who are not authorized cannot start a task. Tag rejects their request
before reading the thread or starting Codex or Claude.

Each authorized caller gets their own agent conversation in a thread, and only
the person who started a task sees its approval prompts and failure messages.

## Sharing a caller is separate from sharing a reply

Anyone who can see the Slack channel can read Tag's requests and replies. That
does not allow them to make Tag do work. Only the owner and explicitly
authorized callers can start a request.

## When to share access

Share access only with people you trust with the local environment and the
connected accounts available to Tag. If a teammate only needs to add context,
review an answer, or take a task from the result, they can do that in the
thread without being authorized to invoke Tag.

To add a caller, append their Slack member ID to the comma-separated
`SLACK_ALLOWED_USER_IDS` setting. Keep your own ID first, then restart:

```sh
tag config set SLACK_ALLOWED_USER_IDS U0OWNER123,U0TEAMMATE45
tag restart
```

For a named Tag, use `tag NAME config set …` and `tag NAME restart`. See the
[Slack adapter reference](../../references/slack-adapter.md#environment)
for the setting and its fail-closed behavior.
