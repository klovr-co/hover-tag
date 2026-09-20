# Offline replies

Tag can keep your own Slack app connected through its shared connection service.
When your computer is stopped, asleep, or disconnected, authorized mentions get:

> Tag is offline. Start Tag on its host computer, then retry.

Run `tag setup` normally. The setup review explains hosted credential storage;
`tag start` handles registration and connection automatically. You do not need a
Cloudflare account, a signing secret, or changes to Slack's request URLs. Tasks
still run on your computer.

`tag stop` stops the local services while leaving offline replies available.
Offline requests are not queued: start Tag and send a new mention to retry.

`tag disconnect` removes the hosted registration and selects direct local Slack
connections. Your Slack app is kept. Start Tag to use it locally again; offline
reminders are unavailable in direct mode. To re-enable hosted connections, choose
`hosted` for Slack connection in Settings, then start Tag.

Reset also removes hosted access before backing up settings. If removal cannot
be confirmed, Tag preserves the ownership credential so you can retry. Keep that
credential private; do not copy one app's Tag settings to a second running host.

Separate Slack apps have separate registrations and access rules, including when
they share a workspace. This version supports one workspace per personally owned
app. Changes to credentials, authorized users or selected channels synchronize on
start/restart. The invited-channel policy checks current bot membership while the
computer is off.

Existing installations stay direct until setup is reviewed again. Existing Slack
apps may need permission approval for `users:read`, used to verify the app's bot
identity. The connection service stores app/bot credentials encrypted; it sees
Slack request payloads but does not persist task text. Metadata expires after one
day. A host can still disconnect after accepting work; this feature does not
recover interrupted tasks.

Service operators can find deployment details in the
[receiver source](https://github.com/klovr-co/tag/tree/main/services/receiver).
