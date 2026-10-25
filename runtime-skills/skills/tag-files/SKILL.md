---
name: tag-files
description: Use in a Tag Slack run before saving any file for the user - an image they asked for, disposable output such as generated HTML, or a lasting file they explicitly asked to create, edit, attach, upload, or keep local.
---

# Files in a Tag run

Paths come from your instructions (workspace root, conversation id, channel
artifact folder, file delivery default) and from the request's "Request
context" (generated images directory, temporary artifacts directory, output
file manifest). Run helpers as your instructions describe.

## Images the user asked for

- Save each final PNG, JPEG, GIF, or WebP file directly in the request's
  generated images directory.
- The Slack bridge uploads supported files from that directory to the current
  thread after your final answer and keeps a copy in the channel artifact
  folder's `images` folder. Do not call Slack's API to upload them.
- Put only final images there, use descriptive filenames, and still describe
  the result concisely in your final answer.

## Disposable artifacts

- Put other disposable task artifacts, including generated HTML, directly in
  the request's temporary artifacts directory, not the workspace. It lives under
  Tag's private temporary home and is removed after the response.

## Lasting files

Only when the user explicitly asks you to create or change a lasting file:

- Save new deliverables in the channel artifact folder by default. It belongs to
  the current Slack channel and is stable across channel renames. Use
  descriptive filenames; check for existing files and avoid overwriting an
  unrelated result from another request in this channel.
- Honor an explicitly requested path inside the workspace and edit existing
  files in place. For an unqualified existing filename, check the channel
  artifact folder first, then the workspace root for older files; ask if the
  reference is ambiguous. Do not move older files or search other channels'
  artifact folders merely to resolve a filename.

When the request lists an output file manifest, make each requested final
deliverable available through Tag:

- After saving it, run the `record_output_artifact.py` helper with
  `--manifest` set to the request's output file manifest, `--workdir` set to the
  workspace root, `--channel-id` set to the conversation id, and `--file` set to
  its absolute path (or a filename relative to the channel artifact folder).
- Call the helper separately for every requested final deliverable, including
  every file in a multi-file request. Never record supporting files or files
  merely mentioned in the conversation. Any regular file type is supported.
- Recording keeps the local file available. With the file delivery default
  `local`, files stay local; with `local+slack`, requested final deliverables
  are also uploaded to Slack. Successful uploads appear as Slack attachments with
  one Open folder button. Individual Open file buttons appear only for
  local-only files or when uploads fail or exceed the size limit.
- Without an explicit delivery instruction, omit delivery flags to use the
  default. Add `--attach` when the user asks to attach, upload, send, return, or
  provide a downloadable copy in Slack. Add `--local-only` when the user asks to
  keep a file local or not upload it. Apply the instruction to every requested
  file.
- Do not record anything if saving fails. If recording fails, say that the file
  was saved but could not be made available through Tag.
- The Slack bridge performs any requested upload after your run. Do not claim a
  file is attached or downloadable until the bridge reports successful delivery.
- Do not mention the manifest helper, its exit code, or manifest state; those
  are internal transport details.
