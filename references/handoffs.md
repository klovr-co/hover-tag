# Handoffs between Tags

One Tag (the sender) can ask other Tags (peers) for help and continue when they
have all replied. Slack carries every message, so the Tags may belong to
different people on different machines, and people can follow the exchange.

## Configuration

`OPENTAG_PEER_TAGS` lists peers as `Name=MEMBERID` pairs (each Tag's bot member
ID). It controls both directions: the Tags this Tag may ask, and the bots whose
requests and replies it accepts. Mentions from other bots are ignored. Empty
disables handoffs; no migration is needed for existing installations.

## Flow

1. The sender's agent runs `scripts/tag_handoff.py ask --to NAME [--to NAME]
   --task TEXT [--wait-minutes N]`. The helper only records the request for this
   run in `OPENTAG_HANDOFF_REQUESTS`; it never calls Slack.
2. After a successful run, the bridge saves a wait record under
   `<instance>/state/handoffs/<id>.json`, posts a status line in the original
   thread, and posts one top-level channel message that mentions every peer and
   ends with `_Handoff <id> for <@requester>. Reply in this thread._`
3. A peer accepts the request only when the sender is in its
   `OPENTAG_PEER_TAGS` and the requester is in its `SLACK_ALLOWED_USER_IDS`. It
   runs as that requester, in the request message's thread, with
   `OPENTAG_HANDOFF_DEPTH=1`, so it cannot ask further Tags. Otherwise it replies
   with a `failed` result.
4. The peer's bridge posts its answer as a new message that starts with
   `<@sender> _Handoff <id> result: completed._` (or `failed`). Streaming is off
   and placeholders are replaced, because Slack sends a mention event only for
   new messages.
5. The sender's bridge records each result in the wait record and edits the
   status line, without starting a model run or mentioning anyone.
6. When every peer has replied, or the deadline passes (checked every 30
   seconds), exactly one caller moves the record to `combining`. The bridge then
   starts one run in the original thread with the original request and the
   request thread's replies, also at depth 1, and marks the wait `completed` or
   `failed`.

Wait records use A2A-style states (`submitted`, `completed`, `failed` per peer)
so another transport could replace Slack without changing the record.

## Limits

- Up to five peers per request, one request per run, a task of up to 3,000
  characters, and a 1 to 120 minute deadline (default 30).
- One handoff step: a peer or a combining run cannot start another handoff.
- Peers do not see the sender's thread; the task must contain what they need.
- A peer's question back to the sender is treated as its reply; the combining
  run passes it on.
- Unverified in a live workspace: Slack delivering `app_mention` events for
  messages posted by another app's bot. Confirm with two connected Tags before
  relying on handoffs.
