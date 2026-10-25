---
name: tag-slack-search
description: Use in a Tag Slack run before searching Slack beyond the current channel (named channels, across Slack, all channels, anything with unclear search scope), or to summarize the current channel's history or next steps.
---

# Slack search scope

`mfs_search.py` stays restricted to the current Slack channel. Choose the scope
from the user's wording:

- Current channel: use `mfs_search.py`.
- Named channels: run the `slack_history_search.py` helper with one
  `--channel NAME` per requested channel. Pass every name exactly as the user
  wrote it. Never silently fix a typo, substitute a different channel, or omit
  one of the requested channels. If the helper rejects a name or suggests a
  correction, ask the user to confirm it and do not retry the search in the
  same run.
- All permitted channels: run `slack_history_search.py` with no `--channel`
  arguments, only when the user clearly says `across Slack`, `all channels`, or
  an equivalent unambiguous phrase.
- Conflicting or fragmentary wording such as `search general workspace all` is
  ambiguous: ask a short scope question and do not call a search helper. A broad
  topic alone never expands the current-channel default.

The helper's runtime grant enforces authorization, and its output identifies
every result's source channel. Preserve that source-channel attribution.

# Summarizing this channel

When the user asks to summarize the current Slack channel, its history, or its
next steps, the current thread is not the whole channel:

1. Find an allowed `slack://` scope and list its `/channels` directory with
   `mfs_ls.py`.
2. Select the entry ending in `__<conversation id>` and read that channel's
   `messages.jsonl` with `mfs_cat.py`.
3. If no matching indexed Slack source exists, say that channel history is
   unavailable.

Treat a bare "summarise this" or "summarize this" as a channel-summary request
when the current thread has no substantive context beyond the request or a
working status reply.
