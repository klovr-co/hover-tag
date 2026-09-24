# Your own Tag

Tag lets you work with your Codex agent from Slack. You ask it for help in
a thread, and it replies there. It can use the tools and accounts connected
to your Codex setup.

You have your own Tag. Maya has Maya's Tag; Jules can have Jules's Tag.
Each person asks their own Tag to work, in the conversations they share.

## Work together in the thread

Jules and Maya are planning a launch. Maya asks Maya's Tag to make a
checklist from their discussion:

> **Maya:** I’ll finish the FAQ by Tuesday.
>
> **Jules:** I’ll test signup on Wednesday. Support still needs a briefing.
>
> **Maya:** @Maya's Tag pull this into a launch checklist with owners.
>
> **Maya's Tag:** Here’s the shared checklist:
> - **Maya:** Finish the FAQ by Tuesday.
> - **Jules:** Test signup on Wednesday.
> - **Unassigned:** Brief support before launch. Who can take this?
>
> **Jules:** I'll brief support. We're also missing the go/no-go review. Maya, can you own that?

Jules can read the checklist, volunteer for a task, and point out what's
missing. He doesn't need to use Maya's Tag to join the discussion.

The request and result stay in the thread, so neither person has to copy
them back from a separate AI chat.

## Only you can give your Tag instructions

By default, only the owner can ask their Tag to do work. Jules can reply to
Maya in the thread, but he can't give Maya's Tag a task.

If you need to let someone else make requests, see
[Sharing access to your Tag](sharing-access.md).

This matters because your Tag may use your files and connected accounts.
For example, if it uses your Gmail connection, it accesses the mail that
connection allows.

## People in the channel can see the work

Anyone who can see the channel can read your requests and Tag's replies.
Keep that in mind when asking about information from a connected account.

Your Tag doesn't automatically see every Slack conversation or account.
[What Tag knows](what-tag-knows.md) explains what it can use, and
[Adding integrations](adding-integrations.md) explains how to connect tools.

## The work happens on your computer

When you ask Tag for help in Slack, Codex runs the task on the computer
where Tag is installed. It uses the local account running Tag, subject to
Codex's permissions. Tools you've connected use their own logins: a Gmail
connection, for example, lets it work with the mail that login can access.

Codex starts in Tag's workspace folder. That folder alone doesn't restrict
it to the files inside; its permissions may also allow it to read or change
files elsewhere on the computer.

The owner-only default doesn't limit what Codex can do on the host computer. To
keep unrelated files and accounts out of reach, consider running Tag on a
separate computer or under a separate local account with limited permissions.
See the [security policy](../../SECURITY.md) for details.
