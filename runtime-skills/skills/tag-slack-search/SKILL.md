---
name: tag-slack-search
description: Use in a Tag Slack run before searching Slack beyond the current channel (named channels, across Slack, all channels, anything with unclear search scope), or to summarize the current channel's history or next steps.
---

# Slack search scope

`slack_search.py` reads Slack live with the Tag's own connection. Nothing it
returns is saved, so search again rather than relying on an earlier search.
Choose the scope from the user's wording:

- Current conversation: `slack_search.py search "QUERY"`.
- Named channels: add one `--channel NAME` per requested channel. Pass every
  name exactly as the user wrote it. Never silently fix a typo, substitute a
  different channel, or omit one of the requested channels. If the helper
  rejects a name, ask the user to confirm it and do not retry the search in the
  same run.
- All of Slack: `--all`, only when the user clearly says `across Slack`, `all
  channels`, or an equivalent unambiguous phrase. It reads the Tag's own
  channels the user may see and searches public channels across the workspace.
- Conflicting or fragmentary wording such as `search general workspace all` is
  ambiguous: ask a short scope question and do not search. A broad topic alone
  never expands the current-conversation default.

Add `--days N` to look further back than 30 days. Slack allows only about ten
public-channel searches a minute, so use one or two well-chosen queries.

The bridge's grant enforces which channels the user may read, and the output
names each result's channel and links to the message. Preserve that
attribution, and say so plainly when nothing matched.

# Summarizing this channel

When the user asks to summarize the current Slack channel, its history, or its
next steps, the current thread is not the whole channel: read it with
`slack_search.py history --days 7` (or the period the user names), then
summarize. For a channel the user named, add `--channel NAME`.

Treat a bare "summarise this" or "summarize this" as a channel-summary request
when the current thread has no substantive context beyond the request or a
working status reply.
