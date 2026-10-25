---
name: tag-handoff
description: Use in a Tag Slack run when the user asks you to involve, ask, or check with other Tags, and your instructions list Tags you may ask.
---

# Asking other Tags

Ask only Tags your instructions list as Tags you may ask.

- Run the `tag_handoff.py` helper once:
  `ask --to "NAME" --to "NAME" --task "TEXT"`, listing every Tag in the same
  call.
- The other Tags cannot see this thread. Put everything they need in `--task`,
  including the exact question and any facts from this conversation.
- Every Tag receives the same `--task`. When they have different parts, name
  each Tag's part, such as "Research Tag: … Writer Tag: …".
- After a successful call, do not do their part yourself. End your reply by
  saying which Tags you asked. Tag posts the request as a new message after your
  reply, the Tags answer under that message, and Tag continues in this thread
  with all their replies; the deadline is `--wait-minutes` (default 30).
- If the helper refuses, explain why and answer as well as you can.
