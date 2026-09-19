# What Tag knows

You can follow up in a Slack thread, ask Tag to find an earlier discussion, or
give it a decision to remember. What it can use depends on the conversation,
the files in its workspace, and the sources and tools configured for it.

## Continue the conversation

Mention Tag in the same thread when you want to build on an answer:

> @Tag turn that into a one-week action plan.

Each mention starts a fresh agent run. Tag reads one page of up to 30 messages
from the current thread and receives your new request separately. That lets
the agent follow the discussion without you copying it into another chat.

This is thread context, not the whole channel. In a long thread, some messages
may be missing from what Tag receives. Restate an important detail if the
answer seems to have lost it.

## Find a discussion in another channel

> @Tag find the launch decision in #product and compare it with the plan in
> your workspace. Show me which messages support the decision.

Tag can search another channel's history when it has been indexed and made
available by the person running Tag. Inviting the bot to a channel does not
automatically make all its history searchable.

The same applies to other indexed sources, such as project documents and
issues. A source may be missing or its index may be out of date. If Tag cannot
find something, that alone does not mean the discussion never happened.

## Ask Tag to remember a decision

> @Tag remember that our weekly report is due Friday.

Tag's runtime instructions tell the agent to save an explicit request like
this as a short note for the current channel and submit it for indexing. They
also tell it to confirm what was saved and report any indexing or access
problem.

A note can be saved locally before it becomes searchable. To retrieve it
through the search helpers on a later task, indexing must finish and the note
must be within the permitted sources. This behavior is handled by the agent
following its instructions; it is not a dedicated Slack command.

Tag does not automatically save a note after every conversation. Files it
writes in the workspace remain available while they are kept there, and
indexed conversations can be searched without turning each one into a note.

## Use the tools already available to the agent

Tag runs your local Codex or Claude CLI. The skills and MCP connections that
the CLI loads can help it carry out a request, alongside installed commands.
Availability depends on the account running Tag, the selected backend's
configuration, and the tool's credentials and permissions.

For example, with the Google Workspace CLI (`gws`) installed and authenticated,
and the Gmail skill available to the agent, you could ask:

> @Tag find the latest email about the launch schedule and summarize what
> changed.

The skill explains how to use `gws`; the Google login determines which email
it can access. Installing a skill alone does not grant access to an account.
These tools use their own permissions, separately from the indexed-source
settings.

## Built on MFS

Tag began as a modified version of the Open Tag example in MFS and uses MFS
to search indexed sources. See the [project attribution](../../README.md#origins-and-attribution)
for its origins and the [memory reference](../../references/memory.md) for saved
note details.
