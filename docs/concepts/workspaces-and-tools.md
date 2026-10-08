# Working with files

Your workspace is the folder where Tag works on your files. Upload a brief in
Slack and ask Tag to save the open questions there. As you answer them, ask Tag
to update the saved file.

This guide works with Codex or Claude Code and assumes you have completed
[your first Tag task](../getting-started/first-task.md). Claude uses the same
workspace with `.claude/skills` and `.mcp.json`.

## Turn a brief into questions you can resolve

Before a launch meeting, attach your existing brief as a text or Markdown file
to a Slack message mentioning Tag. Ask it to flag anything you need to settle
before launch, then answer its questions in the same thread.

> **Iris attached launch-brief.txt**
>
> Launch: Monday. FAQ: Jules, due Tuesday. Signup testing: Iris, due Wednesday.
> Support must be briefed before launch. Go/no-go approval is required.
>
> **Iris:** @Iris's Tag review this and save the open questions as open-questions.md in your workspace and keep the file local.
>
> **Iris's Tag:** Saved the questions in open-questions.md:
> - Are the FAQ and signup testing due the week before launch?
> - Who will brief support, and when?
> - Who makes the final call to launch?
>
> **Iris:** @Iris's Tag yes, both are due the week before. I'll brief support on Thursday. Update the file.
>
> **Iris's Tag:** Updated open-questions.md with those answers. Still open: who makes the final call to launch?
>
> ↗️ open-questions.md · 📁 Open folder

Tag has updated `open-questions.md`. One decision remains: who makes the final
call to launch?

Click ↗️ `open-questions.md` to open the saved file, or **📁 Open folder** to browse
its folder. Both open on the computer running Tag. If someone else hosts Tag,
they open on that person's computer.

Uploading the brief lets Tag read it for the request. It does not automatically
save a copy of the brief in the workspace. In this example, you asked Tag to save
`open-questions.md`. New saved artifacts go in `artifacts/CHANNEL_ID/` inside
your Tag folder, so files from different Slack channels stay separate. Channel
IDs keep these folders stable when channels are renamed. An explicit output
path is respected, and edits to existing files stay in place. Older files remain
where they were saved; Tag does not guess which channel owns them.

> [!NOTE]
> Files saved in the workspace are not automatically indexed. However, Tag can
> still locate and read them using a filename you mention or one already in the
> conversation. See [What Tag knows](what-tag-knows.md) for how indexed search
> works.

## Also attach saved files in Slack

By default, Tag keeps each requested deliverable locally and uploads a copy to
the requesting Slack thread. The reply keeps one **📁 Open folder** button for
access to the local copies. Successful uploads omit individual Open file buttons;
local-only files and files that could not upload keep those file buttons.

To keep saved deliverables local-only, configure your Tag:

```sh
tag config set OPENTAG_FILE_DELIVERY local
tag restart
```

Use `local+slack` to restore the default. Existing installations automatically
adopt this default during startup if no delivery preference is saved. An explicit
`local` setting is preserved. This applies to requested final file deliverables,
not supporting files or every file in the workspace. Generated images retain
their existing delivery behavior.

An individual request can override the default: ask for an attachment to upload
a copy, or explicitly ask to keep the file local to prevent an upload.

Slack attachments are copies, not synchronized files. Later edits to the local
file do not update an earlier attachment. Files above Tag's 15 MiB upload limit
remain available through their local Open button. If Slack delivery fails, Tag
keeps the local file, reports the failure, and continues with other files. Ask
Tag to attach it again after resolving the failure, or to create a smaller copy
for an oversized file.

## Use earlier files in a thread

Tag automatically opens the files on your current message. If your message has
none, it opens the most recent message with files. When a request depends on
other files earlier in the thread, such as "what do you think about both?",
Tag opens those files too, up to 15 MB each. It can open only files shared in
the current channel. If it cannot open a file, it says so instead of
commenting on it.

Images Tag returns in a thread are also kept in the channel's `images` folder
under `artifacts/` in the workspace, so Tag can reopen its own earlier results.

## Use a forwarded Slack file

Tag can read supported files attached to forwarded Slack messages, as well as
files you upload directly. Forward the message containing the file into a
conversation where you use Tag, then mention Tag in that thread and explain
what you want it to do with the file.

The file must be accessible to Tag through Slack. If Tag cannot retrieve it,
attach a copy you intend to share directly in the task thread. Forwarding a
message does not give Tag access to the rest of the original conversation.

Forwarded files use the same attachment limits as direct uploads: downloaded
files are limited to 15 MiB each, and text included in the request is truncated
at 12,000 characters per item. Reading images and other file formats also
depends on the backend's available tools. Tag reads bounded thread context, so
keep the file and request together in a short thread.

Like a direct upload, forwarding a file does not automatically save it in your
workspace. Ask Tag to save a copy if you want to keep working on it later.

## Find your workspace

The workspace lives on the computer running Tag. If someone else hosts Tag for
your team, the folder is on their machine. In the Tag app, open the Tag, choose
**Details**, and click **Show** next to **Working folder**. From a terminal,
run:

```sh
tag paths
```

Find the `workspace` path in the output. A normal installation names each Tag's
folder after its Slack workspace and app IDs, in lowercase, such as
`~/Tag/t0abc123-a0xyz789`. Tags created by earlier releases in `~/Tag/default`
or `~/Tag/NAME` are moved there automatically once their Slack app is known.
Custom installations can use a different location, so use the path Tag reports.

Tag keeps this folder separate from its application releases. Files and local
skills stay in place when you upgrade. Its hidden `.tag` directory holds this
Tag’s settings, credentials, integrations, and conversation state. Keep that
directory with the working files when moving the folder. You can organize it like any working
folder:

```text
workspace/
  artifacts/
    C0123456789/
      open-questions.md
  meeting-notes/
  templates/
    weekly-report.md
```

The workspace is the agent's starting directory. Its actual file and command
access depends on the backend's permissions; the folder itself is not a
sandbox. See the [security policy](../../SECURITY.md) for the execution model.

## Keep working on the result

Files Tag saves are ordinary files. Open the checklist in your editor, make
changes yourself, or use it in another tool. On a later request, tell Tag which
file to read so it can work from the current version. Your work stays in your
working folder when Tag is upgraded.
