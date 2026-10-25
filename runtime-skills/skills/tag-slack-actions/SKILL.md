---
name: tag-slack-actions
description: Use in a Tag Slack run when the user explicitly asks to post, send, or share a message in this Slack channel, or to create a Slack Canvas.
---

# Slack actions in a Tag run

Both helpers are already restricted to the channel that triggered this
@mention. Never call Slack's HTTP API directly, use them for another channel, or
expose or print Slack tokens.

## Post a channel message

- Only when the user explicitly asks to post, send, or share a message in this
  channel, run the `slack_post_message.py` helper with `--text`. This creates a
  new top-level channel message, not a thread reply.
- Do not post merely because you produced a summary; post only when the user
  expressly requested the channel message.
- State in your final answer whether the post succeeded.

## Create a Canvas

- Only when the user asks to create a Canvas in this channel, write a Markdown
  file in the workspace and run the `slack_canvas.py` helper with `--title` and
  `--markdown-file`.
- Do not create, edit, delete, or share a Canvas unless the user explicitly asks
  for that action. Report the resulting Canvas URL when Slack provides one.
