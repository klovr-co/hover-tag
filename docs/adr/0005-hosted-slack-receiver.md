# ADR 0005: Shared Socket Mode receiver integrated with setup and start

- Status: Accepted
- Feature: https://github.com/klovr-co/tag/issues/48

## Context

Each Tag user owns a separate Slack app. Requiring that user to deploy a Worker,
copy a signing secret, or change Slack request URLs does not fit the setup flow.
The earlier single-app HTTP receiver design is superseded by this decision.

## Decision

The Tag-operated Cloudflare service holds each registered app's Socket Mode
connection. One SQLite-backed Durable Object is keyed by app ID, with an explicit
workspace identity. Different apps in the same workspace are isolated. A single
app distributed across several workspaces is not supported by this first version.

The normal setup review discloses hosted credential storage and saves the hosted
connection choice. Start registers/synchronizes app and bot tokens and access
rules, then opens the local outbound TLS WebSocket. Existing installations stay
direct until setup is reviewed; stop leaves the hosted connection alive.
Disconnect and reset remove hosted registration before discarding local ownership.

Registration validates bot identity using auth.test and bots.info; users:read is
required. The Slack hello handshake binds the app-level token to the selected app.
An ownership token saved locally before the first request makes interrupted
registration retry-safe and prevents another client overwriting an existing app.
The service stores its hash, not the token. Slack credentials are AES-GCM encrypted
with a service secret and the app ID as authenticated context. Mutation endpoints
require ownership; registration is rate-limited.

The receiver checks workspace, app, users and channels for each request. Invited
policy checks current bot membership; local Tag checks its own policy again. A
short round-trip probe determines whether to forward a request or send the offline
reminder. Durable event ownership prevents a Slack retry switching between local
execution and an offline response. Offline requests never replay on reconnect.

Recent local receiver heartbeats report whether its Slack connection is live.
Together with MFS health they extend ADR 0002's direct Socket Mode readiness rule.
The receiver uses durable watchdog alarms for Slack reconnects independently of
the local host. Relay ownership credentials are excluded from backend environments
under ADR 0001; backend execution still occurs locally.

## Consequences

- Setup/start manage the hosted integration. Users need no Cloudflare account,
  signing secret or HTTP request-URL cutover. Slack permission approval remains
  part of ordinary app setup.
- The service is trusted with Slack credentials and event payloads; setup must
  disclose that boundary. Credential removal and failed-removal recovery are part
  of the lifecycle, not an operator-only task.
- Outgoing Slack WebSockets do not hibernate. The shared operator must budget for
  duration and watchdog activity per registered app.
- Accepted tasks are not replayed after local failure. An ambiguous successful
  Slack post can be duplicated by an outbox retry; exactly-once delivery is not
  promised. One-day metadata retention does not include task text.
- Real multi-app Slack acceptance is required before general rollout. Automated
  protocol tests alone do not establish end-to-end product readiness.
