# Your own Tag

Tag lets you work with your Codex agent from Slack. You ask it for help in
a thread, and it replies there. It can use the tools and accounts connected
to your Codex setup.

You have your own Tag. Jules has Jules's Tag; Maya can have Maya's Tag.
Each person asks their own Tag to work, in the conversations they share.

## Work together in the thread

Jules and Maya are planning a launch. Jules asks Jules's Tag to make a
checklist from their discussion:

> **Jules:** I’ll finish the FAQ by Tuesday.
>
> **Maya:** I’ll test signup on Wednesday. Support still needs a briefing.
>
> **Jules:** @Jules's Tag pull this into a launch checklist with owners.
>
> **Jules's Tag:** Here’s the shared checklist:
> - **Jules:** Finish the FAQ by Tuesday.
> - **Maya:** Test signup on Wednesday.
> - **Unassigned:** Brief support before launch. Who can take this?
>
> **Maya:** I'll brief support. We're also missing the go/no-go review. Jules, can you own that?

Maya can read the checklist, volunteer for a task, and point out what's
missing. She doesn't need to use Jules's Tag to join the discussion.

The request and result stay in the thread, so neither person has to copy
them back from a separate AI chat.

## Only you can give your Tag instructions

Today, only the owner can ask their Tag to do work. Maya can reply to Jules
in the thread, but she can't give Jules's Tag a task. Sharing one Tag with
other people is coming soon.

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

Only you can request work from your Tag, but that rule doesn't limit what
Codex can do on the host computer. To keep unrelated files and accounts out
of reach, consider running Tag on a separate computer or under a separate
local account with limited permissions. See the [security policy](../../SECURITY.md)
for details.
