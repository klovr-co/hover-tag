# Working with files

Your workspace is the folder where Tag works on your files. Upload a brief in
Slack and ask Tag to save the open questions there. As you answer them, ask Tag
to update the saved file.

This guide uses Codex and assumes you have completed
[your first Tag task](../getting-started/first-task.md). Claude remains
experimental.

## Find your workspace

The workspace lives on the computer running Tag. If someone else hosts Tag for
your team, the folder is on their machine. On the computer running Tag, open a
terminal and run:

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

Before a launch meeting, attach your existing brief as a text or Markdown file
to a Slack message mentioning Tag. Ask it to flag anything you need to settle
before launch, then answer its questions in the same thread.

> **Maya attached launch-brief.txt**
>
> Launch: Monday. FAQ: Jules, due Tuesday. Signup testing: Maya, due Wednesday.
> Support must be briefed before launch. Go/no-go approval is required.
>
> **Maya:** @Maya's Tag review this and save the open questions as open-questions.md in your workspace.
>
> **Maya's Tag:** Saved the questions in open-questions.md:
> - Are the FAQ and signup testing due the week before launch?
> - Who will brief support, and when?
> - Who makes the final call to launch?
>
> **Maya:** @Maya's Tag yes, both are due the week before. I'll brief support on Thursday. Update the file.
>
> **Maya's Tag:** Updated open-questions.md with those answers. Still open: who makes the final call to launch?

Tag has updated `open-questions.md`. One decision remains: who makes the final
call to launch?

Uploading the brief lets Tag read it for the request. It does not automatically
save a copy of the brief in the workspace. In this example, you asked Tag to save
`open-questions.md`.

Tag knows the filename here, so it can read and update `open-questions.md`
directly. Later, you might ask it to find the unanswered launch questions
without knowing which file contains them. MFS can help with that search if the
operator has included the file in an indexed, permitted source. Saving the file
does not automatically index it. See [What Tag knows](what-tag-knows.md) for how
indexed sources work.

Use your own Tag's mention in place of `@Maya's Tag`.

To connect Tag to services such as Gmail, see [Adding integrations](adding-integrations.md).
