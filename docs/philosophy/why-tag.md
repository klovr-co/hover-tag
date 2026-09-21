# Why Tag

## Work already happens in Slack

Most team work does not begin as a tidy brief. It starts in a Slack thread: a
question, a few links, some back-and-forth, and eventually a decision that
someone needs to act on.

Moving that work into a private AI chat creates an annoying little relay job.
One person has to retell the conversation, bring the answer back, and explain
what the agent actually did. Tag removes that handoff.

> @Tag, Slack is all you need.

Mention Tag where the conversation is happening. Anyone in the thread can add
context, question the result, or pick up the next step. There is no second AI
inbox to check.

## Ask for the outcome

Tag can answer a question, but it becomes much more useful when you give it a
job with an outcome.

```text
Question
"What did we decide about the launch date?"

Delegation
"Find the launch decision, compare it with the open issues, and prepare an
update with owners and unresolved risks."
```

The agent can check permitted sources, use the tools installed with it, and work
with files in its workspace. The result might be a short answer. It might also
be an updated file, a project brief, or a decision your team can act on.

## Shared work beats private AI history

A private AI chat is convenient, but it puts the useful part of the work behind
one person's account. When the request starts in Slack, the team can see what
was asked and continue from the same result. Colleagues can add context before
the work goes too far, and someone else can take over without reconstructing a
private conversation.

Tag uses the operator's locally authenticated Codex or Claude CLI. It does not
require the team to move into a separate hosted agent interface.

## Context you can account for

The Slack thread supplies the immediate conversation. When Tag needs something
outside the thread, it searches sources that have been indexed through MFS and
allowed for that installation. Connecting a source and allowing Tag to search
it are separate choices.

This is less magical than saying the model "remembers everything," and that is
the point. You can see where the context lives and decide whether Tag should be
able to retrieve it.

## Fresh work, reconstructible context

Each mention starts a fresh CLI agent run. Continuity comes from information
that can be reconstructed: the current Slack thread, the configured workspace,
approved sources, and any saved settings for that thread.

The model itself does not quietly build a permanent memory of every request.

## Next

Read [How Tag works](../concepts/mental-model.md) to see how Slack, the agent
workspace, and MFS fit together.
