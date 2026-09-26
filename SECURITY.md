# Security policy

Tag is alpha software that runs a local coding agent in response to chat
messages. It is not a production security boundary. Use a dedicated host or
sandbox workspace, an isolated chat channel, least-privilege credentials, and
explicit MFS scope allowlists.

## Credential boundary

The agent backend inherits the bridge process environment, including
`SLACK_BOT_TOKEN`, `MFS_TOKEN`, and unrelated ambient credentials. Tag removes
`SLACK_APP_TOKEN`, `SLACK_CHANNEL_ID`, `SLACK_CHANNEL_IDS`, and
`SLACK_ALLOWED_USER_IDS` before
starting backend work, but it does not broker the remaining credentials.

Consequently, Slack and MFS helper allowlists are guardrails for normal agent
operation, not hardened capability controls: an agent with shell access can use
inherited credentials directly. Run Tag under a dedicated local account or
external sandbox and give every credential only the permissions appropriate
for that environment.

For cross-channel history search, the bridge—not the model—intersects stable
channel IDs across the configured workspace, operator approval, indexed MFS
scopes, and live caller visibility before launching the backend. The model can
choose to invoke the cross-channel helper, but that helper can search only the
bridge-generated grant; failures deny expansion. This is still an application
guardrail under ADR 0001, not a credential broker or hardened isolation
boundary.

## Local storage

Normal installations store each Tag's settings, credentials, and history in
`~/Tag/NAME/.tag`. Tag makes this directory private to the local account and
places a `.gitignore` inside it to exclude its contents. Forced Git additions
can bypass that exclusion. Cloud syncing the working folder also syncs its
credentials. The directory is not an access boundary against the coding agent
or other processes running as the same user.

## Reporting a vulnerability

Please do not disclose a suspected vulnerability in a public issue. Use
[GitHub's private vulnerability reporting](https://github.com/klovr-co/hover-tag/security/advisories/new)
and include:

- the affected commit or release;
- reproduction steps and expected impact;
- whether credentials, agent permissions, or scope boundaries are involved;
- any suggested mitigation.

We will acknowledge a report when it is reviewed, but this alpha release does
not promise a response or remediation service level.

## Supported versions

Only the latest published prerelease is considered for security fixes. Until a
stable release exists, updates may require configuration changes.
