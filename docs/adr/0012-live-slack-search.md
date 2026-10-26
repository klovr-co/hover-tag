# ADR 0012: Search Slack live with the Tag's own bot connection

- Status: Accepted
- Date: 2026-10-26
- Supersedes: the shared local Slack index (MFS) in ADR 0002 and ADR 0005
- Relates to: ADR 0001 (credential boundary), ADR 0011 (markdown memory with
  history)

## Context

A Tag found older Slack discussions through a local search server. The
installer downloaded a 560 MB embedding model, a Milvus server ran beside the
Tags, and each Tag's channels were copied into that index with a separate
history credential. That meant a large install, a service to keep running, a
second Slack credential per Tag, and copies of Slack messages on the computer.

Slack now offers a search method for AI apps, the Real-time Search (RTS) API.
Before building on it we checked what docs.slack.dev says about it on
26 October 2026.

## What Slack's documentation says

Sources:
[Real-time Search API](https://docs.slack.dev/apis/web-api/real-time-search-api),
[`assistant.search.context`](https://docs.slack.dev/reference/methods/assistant.search.context),
[`search:read.public`](https://docs.slack.dev/reference/scopes/search.read.public),
[rate limits](https://docs.slack.dev/apis/web-api/rate-limits),
[`conversations.history`](https://docs.slack.dev/reference/methods/conversations.history),
[`conversations.replies`](https://docs.slack.dev/reference/methods/conversations.replies) and the
[2025 rate limit changes](https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps).

- **Who may use it.** "The RTS API is available for directory-published apps
  and internal apps only." Unlisted distributed apps may not. Each Tag is its
  own app, created in one workspace from Tag's manifest and never distributed,
  so it is an internal app.
- **Tokens.** `search:read.public` works with a bot token. Searching private
  channels, group DMs and DMs needs `search:read.private`, `search:read.mpim`
  and `search:read.im`, which are user-token scopes only.
- **`action_token`.** "All API calls made using a bot token require an
  `action_token`." Slack includes one in `app_mention` and in message events
  when the app is mentioned, and in every DM to the app. The docs don't state
  how long it lasts.
- **Rate limits.** "For most teams, the limit is 10+ requests per minute",
  with "an additional user-level limit of 10 requests per minute". Paginated
  calls count. `limit` is "up to a max of 20".
- **Guests.** "Workspace guests are not permitted to access apps using
  platform AI features, and therefore, the Real-time Search API."
- **Storage.** "You must not store or copy any of the data retrieved from
  this API." Slack's API Terms also allow only limited, temporary handling
  that is deleted promptly.
- **History methods.** `conversations.history` and `conversations.replies`
  are Tier 3 (50+ per minute). The 2025 limits of 1 request a minute apply
  only to commercially distributed apps outside the Marketplace; "Internal
  customer-built applications are not impacted by these changes."

## Decision

A Tag searches Slack live, with its own bot token, and stores nothing it reads.

- **Public channels** use `assistant.search.context` with bot scope
  `search:read.public` and the `action_token` from the event that started the
  run. A Tag searches all public channels only when the person asked for it in
  their own words, such as "search all channels". Tag keeps under 10 searches
  a minute per Tag and asks for at most 20 results a call.
- **Private channels and DMs the Tag is in** use `conversations.history` and
  `conversations.replies`, read into memory and matched there. This needs no
  user token, so there is no personal Slack sign-in step.
- **Nothing is stored.** Search results go to the run's output and nowhere
  else. The only file is a list of recent call times, used to keep under
  Slack's rate limit.
- **No `action_token`, or a guest asked.** Public search is skipped and the
  Tag says why; channels the Tag is in are still read with the history
  methods.
- The local search server, the embedding model, the history credential and
  the stored Slack copies are removed from Tag.

## Upgrading

`tag start` removes each Tag's index settings, connector and history
credential once. While the old server still runs, Tag first asks it to drop
every Tag's Slack copies, then stops it. The server's folder may hold other
data, so Tag leaves it and says where it is; `tag memory remove-old-index
--yes` deletes Tag's index files there. `tag rollback` to a release that
needs the old index is refused after this step.

## Consequences

- Install is about 560 MB smaller and has no background download. There is no
  memory service to start, stop or keep running.
- Results reflect Slack as it is now, including edits and deletions, and the
  person's channel access is not widened: a Tag reads only channels it is a
  member of, plus public search when asked.
- A search costs Slack calls on each request. Public search allows about ten
  calls a minute, so the Tag searches in fewer, broader calls.
- Public search works only on requests that come from Slack with an
  `action_token`. Routines and the app's own runs read channels with the
  history methods only.
- The AI backend may keep its own session transcript of what the agent read
  during a run. That transcript is the backend's, under the Tag's private
  home, and follows the backend's own retention. Tag doesn't add Slack
  content to it beyond what the run needed.
