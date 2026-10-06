# Sharing access to your Tag

Your Tag starts with you as its only authorized caller. You can deliberately
authorize additional Slack members when you want them to ask your Tag for work.

## What sharing access means

An authorized caller can start work with the same local agent environment that
your Tag uses. That can include its workspace files, installed tools, and
connected accounts, subject to the backend's own permissions. Treat adding a
caller as granting access to ask that environment to act, not merely permission
to read a Slack conversation.

People who are not authorized cannot start a task. Tag rejects their request
before reading the thread or invoking the backend.

## Sharing a caller is separate from sharing a reply

Anyone who can see the Slack channel can read Tag's requests and replies. That
does not allow them to make Tag do work. Only the owner and explicitly
authorized callers can start a request.

## When to share access

Share access only with people you trust with the local environment and the
connected accounts available to Tag. If a teammate only needs to add context,
review an answer, or take a task from the result, they can do that in the
thread without being authorized to invoke Tag.

## Letting other Tags ask your Tag

Tags can ask each other for help in a channel they share. Your Tag accepts a
request from another Tag only when both of these are true:

- You listed that Tag in `OPENTAG_PEER_TAGS`.
- The person who asked the other Tag is also authorized to use your Tag.

Your Tag then works for that person, with your Tag's environment and accounts,
exactly as if they had asked it directly. Listing a peer also lets your Tag ask
that Tag. Ask the other Tag's owner to list your Tag too.

To find a Tag's bot member ID, open the Tag's profile in Slack, select **More**,
and choose **Copy member ID**. Then run:

```bash
tag config set OPENTAG_PEER_TAGS "Research Tag=U0123ABCD,Writer Tag=U0456EFGH"
tag restart
```

To add a caller, append their Slack member ID to the comma-separated
`SLACK_ALLOWED_USER_IDS` setting. See the
[Slack adapter reference](../../references/slack-adapter.md#environment)
for the setting and its fail-closed behavior.
