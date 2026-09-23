# Tag

## @Tag in Slack

Your personal assistant, in your workspace.

[Install Tag](getting-started/first-task.md)

**Ask your agent to set up Tag**

Copy this prompt into your coding agent:

```text
Install the hover-tag-setup skill from https://github.com/klovr-co/hover-tag, then use it to set up Tag for me.
Help me connect Tag to Slack and guide me through any login or authorization steps I need to complete myself.
```

Jules and Maya discuss the launch, then Maya asks their Tag to pull it
together:

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

## Who it's for

Bring your own Tag to work. Tag is for people who want to use their Codex
agent in Slack, with context from Slack threads and integrations already
connected to their agent. Requests, updates, and results stay in the thread,
where teammates can follow the work.

You don’t need to code to use Tag. Follow the
[setup guide](getting-started/first-task.md) to connect your agent to Slack.

By default, only you can ask your Tag to work, because it uses your agent's
available files, tools, and connected accounts. Teammates can still see the
conversation. Read more about [your own Tag](concepts/access.md).

## What you can delegate

You can ask Tag to:

- summarize a discussion and turn it into decisions, owners, and next steps;
- investigate a question across approved Slack history and other sources;
- compare information from conversations, documents, issues, and repositories;
- use locally installed tools available to Codex;
- inspect or change files in its configured workspace when explicitly asked;
- return an answer to the thread, post to the channel, or create a Slack Canvas.

What Tag can do depends on its setup. It can use the current thread, files in
its workspace, and the sources and tools made available to it. See
[What Tag knows](concepts/what-tag-knows.md) for examples and limits.

## Start here

1. Read [Why Tag](philosophy/why-tag.md) for the product philosophy.
2. Read [How Tag works](concepts/mental-model.md) to see what happens after a
   mention.
3. Follow [Run your first task](getting-started/first-task.md) to install Tag and
   complete one useful Slack workflow.

## Find the right documentation

| I want to… | Read |
| --- | --- |
| Set up Tag and try a first task | [Get started](getting-started/first-task.md) |
| Understand why I’d bring my assistant into Slack | [Why Tag](philosophy/why-tag.md) |
| Know who can use my Tag and see its replies | [Your own Tag](concepts/access.md) |
| Let someone else make requests to my Tag | [Sharing access to your Tag](concepts/sharing-access.md) |
| See what happens when I ask for help | [How Tag works](concepts/mental-model.md) |
| Follow up or find an earlier discussion | [What Tag knows](concepts/what-tag-knows.md) |
| Work with attachments and save files | [Working with files](concepts/workspaces-and-tools.md) |
| Add my tools, skills, and connected accounts | [Adding integrations](concepts/adding-integrations.md) |
| Find an email and draft a reply from Slack | [Use Gmail from Slack](tutorials/use-gmail-from-slack.md) |
| Check what’s supported today | [Supported capabilities](reference/supported-capabilities.md) |
| Fix a setup or connection problem | [Troubleshooting](troubleshooting.md) |

For detailed configuration, see the [Slack adapter reference](../references/slack-adapter.md).
Read the [security policy](../SECURITY.md) for permissions and operating limits.
