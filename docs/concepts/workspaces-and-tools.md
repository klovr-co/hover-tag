# Working with files

Your workspace is the folder where Tag works on your files. Upload a brief in
Slack and ask Tag to save the open questions there. As you answer them, ask Tag
to update the saved file.

This guide uses Codex and assumes you have completed
[your first Tag task](../getting-started/first-task.md). Claude remains
experimental.

## Find your workspace

In your terminal, run:

```sh
tag paths
```

Find the `workspace` path in the output. On a default macOS installation it is
`~/Library/Application Support/Tag/workspace`. Other platforms and custom
installations use different locations, so use the path your command reports.

Tag keeps this folder separate from its application releases. Files and local
skills stay in place when you upgrade. You can organize it like any working
folder:

```text
workspace/
  open-questions.md
  meeting-notes/
  templates/
    weekly-report.md
```

The workspace is the agent's starting directory. Its actual file and command
access depends on the backend's permissions; the folder itself is not a
sandbox. See the [security policy](../../SECURITY.md) for the execution model.

## Turn a brief into questions you can resolve

Before a launch meeting, upload your existing brief to Slack as a text or
Markdown file. Ask Tag to flag anything you need to settle before launch.

> **Maya attached launch-brief.txt**
>
> Launch: Monday. FAQ: Jules, due Tuesday. Signup testing: Maya, due Wednesday.
> Support must be briefed before launch. Go/no-go approval is required.
>
> **Maya:** @Tag review this and save the open questions in your workspace.
>
> **Tag:** Saved the questions in open-questions.md:
> - Are the FAQ and signup testing due the week before launch?
> - Who will brief support, and when?
> - Who makes the final call to launch?
>
> **Maya:** @Tag yes, both are due the week before. I'll brief support on Thursday. Update the file.
>
> **Tag:** Updated open-questions.md with those answers. Still open: who makes the final call to launch?

The questions and your answers are now in one file. You can pick it up at the
meeting or ask Tag to update it when the last decision is made.

Use your installed bot's actual mention in place of `@Tag`. Reading and saving
workspace files does not require indexing them first; see
[What Tag knows](what-tag-knows.md) for how indexed sources differ.

To connect Tag to services such as Gmail, see [Adding integrations](adding-integrations.md).
