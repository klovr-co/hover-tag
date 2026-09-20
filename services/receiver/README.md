# Shared Tag connection service

Tag users run `tag setup` and `tag start`. They do not deploy a Worker, enter relay
URLs, copy signing secrets, or configure Slack HTTP endpoints.

During the ordinary setup review, Tag explains that the hosted service stores its
Slack app credentials encrypted and keeps the app connected while the computer is
offline. Setup saves this choice; start registers or refreshes the app and opens
an authenticated outbound connection. Slack remains in Socket Mode.

When an authorized user mentions an app whose local host is unreachable, that
app replies in the original thread:

> Tag is offline. Start Tag on its host computer, then retry.

## User lifecycle

- `tag setup`: normal Slack authorization, app and channel choices, and one setup
  review. No Cloudflare steps. Setup without starting saves choices only.
- `tag start` / `tag restart`: register the selected app, synchronize credentials
  and access rules, and connect. Retries reuse the saved ownership credential.
- `tag stop`: stop local work. The hosted service stays connected and sends
  offline reminders. Offline mentions never become queued local tasks.
- `tag disconnect`: stop the local bridge, remove hosted credentials and delivery
  metadata, revoke its local connection, and select direct local Socket Mode.
  The Slack app and local Slack credentials are retained. `tag start` then runs
  directly, without hosted offline replies.
- `tag reset`: remove hosted registration before archiving local setup. If remote
  removal fails, keep local settings so removal can be retried. Corrupt local
  settings are still recoverable through the reset backup; hosted removal cannot
  be confirmed without a readable ownership credential.

Existing installations remain direct until setup is reviewed again. Hosted mode
requires `users:read` to verify the bot token belongs to the selected app. New app
manifests include it; existing apps may need Slack permission approval during setup.
Changing an app removes the previous registration on the next start. Changes to
users, channels, or credentials synchronize on start/restart. Invited-channel
policy checks current Slack membership even while the local host is offline.
Only one local host can be connected to an app at a time.

## Service operator deployment

The CI workflow deploys this shared service after all policy, Python, and receiver
checks pass on pushes to `main`. Manual CI runs on `main` also deploy; pull requests
only test and build. Production deployments are serialized and are not cancelled
mid-deployment. The source directory name is independent of the Worker identity.

One-time GitHub setup: add the repository Actions secret `CLOUDFLARE_API_TOKEN`
with permission to deploy the receiver in the Klovr account. The account ID is
configured in the workflow. Local Wrangler OAuth login does not authenticate CI.
See [Cloudflare's GitHub Actions guide](https://developers.cloudflare.com/workers/ci-cd/external-cicd/github-actions/).
The existing `CREDENTIAL_KEY` stays in the Worker secret store; deployment must
not regenerate it or existing registrations will become unreadable.

For initial provisioning or a manual deployment, the Tag service operator runs
these commands from `services/receiver`:

```sh
npm ci --legacy-peer-deps
npm test
npm run check
npx wrangler login
npx wrangler secret put CREDENTIAL_KEY
npm run deploy
```

`CREDENTIAL_KEY` is a base64-encoded random 32-byte AES key. Generate it privately,
store it in the operator's secret manager, and supply it through Wrangler's hidden
input. Do not replace it while registrations exist: old records require the same
key to decrypt. Each registration stores encrypted app/bot tokens in its own
Durable Object. No per-user Wrangler variables or secrets are needed.

The shipped client uses `RECEIVER_URL` in `scripts/tag_receiver.py`. The reference
deployment is `https://tag-offline-receiver.maxine-1ef.workers.dev`. A different
service deployment requires distributing clients with the corresponding endpoint.
This endpoint receives Slack payloads and credentials and must be trusted by users.

Registration is rate-limited and requires Slack to verify the bot's workspace and
app. A high-entropy, client-generated ownership token is saved before registration
and stored only as a hash remotely. Updates and removal require that token. The
Socket Mode handshake additionally verifies the app-level token before accepting
local work. Credential encryption uses the app ID as authenticated context.

## Protocol and isolation

One Durable Object is keyed by each Slack app ID. Separate apps in the same
workspace are isolated. This supports Tag's model of one personally owned app per
workspace, not a single Slack app distributed across multiple workspaces; a second
workspace registration for the same app is rejected.

- `PUT /v1/apps/{app_id}/registration`: authenticated create/update with Slack
  credential proof, workspace identity, users, channels and policy.
- `GET /v1/apps/{app_id}/registration`: authenticated registration/readiness status.
- `DELETE /v1/apps/{app_id}/registration`: authenticated, retry-safe removal.
- `GET /v1/apps/{app_id}/connect`: authenticated local WebSocket upgrade.

The receiver holds the sole Slack Socket Mode connection. Do not run older direct
Tag bridges or other Socket Mode clients for the same app; Slack distributes
requests between connections. Local Tag still checks its own authorization rules.

## Reliability and operations

Durable alarms reconnect Slack after connection loss, eviction or deployment,
independently of local Tag. Outgoing Slack sockets prevent hibernation and incur
Cloudflare duration charges. The service also uses regular watchdog alarms; budget
and monitor the shared service as the number of registered apps grows.

Event IDs suppress normal Slack retries for 24 hours. A durable outbox retries
failed offline posts up to six times on watchdog ticks, approximately every 20
seconds. A timeout after Slack accepted a post can still produce a duplicate
reminder. Exactly-once external delivery is not claimed.

A host can disconnect after accepting work. Accepted requests are not replayed;
this is an offline reminder, not task recovery. Metadata expires after one day;
task text is forwarded, not persisted. Disconnect deletes active credential and
metadata records; Cloudflare's storage backup retention remains provider-managed.

Before wider rollout, verify two real apps independently: online tasks, offline
reminders, interactive controls, stop/start recovery, and disconnect. Unit tests
mock Slack API calls and do not substitute for live workspace acceptance.
