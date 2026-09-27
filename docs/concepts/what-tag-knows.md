# What Tag knows

You can follow up in a Slack thread, ask Tag to find an earlier discussion, or
give it a decision to remember. What it can use depends on the conversation,
the files in its workspace, and the sources and tools configured for it.

## Continue the conversation

Mention Tag in the same thread when you want to build on an answer:

> **Maya:** @Maya's Tag Our launch is next Monday. Jules owns the FAQ by
> Tuesday, and I'll brief support on Thursday. What's still open?
>
> **Maya's Tag:** Signup testing still needs an owner and a deadline.
>
> **Maya:** @Maya's Tag turn that into a one-week action plan.
>
> **Maya's Tag:** Tuesday: Jules finishes the FAQ. Wednesday: assign signup
> testing and set its deadline. Thursday: Maya briefs support. Friday: review
> testing and open risks before Monday's launch.

Each mention starts a fresh agent run. Tag reads one page of up to 30 messages
from the current thread and receives your new request separately. That lets
the agent follow the discussion without you copying it into another chat.

This is thread context, not the whole channel. In a long thread, some messages
may be missing from what Tag receives. Restate an important detail if the
answer seems to have lost it.

## Find a discussion in another channel

> **Iris:** @Iris's Tag find the launch decision in #product and compare it with
> the plan in your workspace. Show me which messages support the decision.
>
> **Iris's Tag:** In the indexed #product thread, Maya wrote, "Let's move launch
> to Monday so support has time to prepare." Jules replied, "I'll update the
> checklist for Monday." The workspace plan still says Friday, so it conflicts
> with those messages.

Tag can search another channel's history when it has been indexed and made
available by the person running Tag. Inviting the bot to a channel does not
automatically make all its history searchable.

The same applies to other indexed sources, such as project documents and
issues. A source may be missing or its index may be out of date. If Tag cannot
find something, that alone does not mean the discussion never happened.

## Ask Tag to remember a decision

> @Rowan's Tag save our Friday report deadline in reporting-notes.md.
>
> **Rowan's Tag:** Saved in reporting-notes.md: Our weekly report is due Friday.

Tag does not provide a dedicated saved-note command or local memory store.
For a decision you need later, ask Tag to write it to a specific workspace
file. To make that file searchable through MFS, the operator must include it
in an indexed, permitted source. Saving a file and indexing it are separate
steps; ask Tag to confirm what it actually completed.

Tag does not automatically save a note after every conversation. Files it
writes in the workspace remain available while they are kept there, and
indexed conversations can be searched without turning each one into a note.

## Use the tools already available to the agent

Tag runs your local Codex agent. Claude support is coming soon.
The skills and MCP connections that
the CLI loads can help it carry out a request, alongside installed commands.
Availability depends on the account running Tag, the selected backend's
configuration, and the tool's credentials and permissions.

For example, with the Google Workspace CLI (`gws`) installed and authenticated,
and the Gmail skill available to the agent, you could ask:

> **Zara:** @Zara's Tag find the latest email about the launch schedule and
> summarize what changed.
>
> **Zara's Tag:** The latest matching email is from Maya, subject "Updated launch
> schedule." It moves launch from Friday to Monday so support has more time.
> The FAQ and signup-testing deadlines are unchanged.

The skill explains how to use `gws`; the Google login determines which email
it can access. Installing a skill alone does not grant access to an account.
These tools use their own permissions, separately from the indexed-source
settings.

## Built on MFS

Tag began as a modified version of the Open Tag example in MFS and uses MFS
to search indexed sources. See the [project attribution](../../README.md#origins-and-attribution)
for its origins and the [memory reference](../../references/memory.md) for
retrieval details.
