# Security policy

Tag is alpha software that runs a local coding agent in response to chat
messages. It is not a production security boundary. Use a dedicated host or
sandbox workspace, an isolated chat channel, least-privilege credentials, and
explicit MFS scope allowlists.

## Reporting a vulnerability

Please do not disclose a suspected vulnerability in a public issue. Use
[GitHub's private vulnerability reporting](https://github.com/klovr-co/tag/security/advisories/new)
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
