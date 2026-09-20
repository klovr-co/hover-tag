# Tag domain context

Tag is a self-hosted bridge that lets authorized Slack users delegate work to
a local coding-agent backend with optional MFS retrieval.

## Glossary

- **Tag** — the product and repository. Use this name in new prose.
- **OpenMax** — the default Slack bot display name. It is an identity inside
  Tag, not a synonym for the product.
- **Slack bridge** — the long-running local process that receives Slack events,
  enforces caller policy, invokes a backend, and renders replies. Hosted mode
  connects to the shared receiver; direct mode connects to Slack Socket Mode.
- **Hosted receiver** — the shared Tag service that maintains an isolated Socket
  Mode connection for each registered app and sends offline reminders.
- **Backend** — the local Codex or experimental Claude CLI process that performs
  an agent task.
- **MFS scope** — an operator-configured URI root used by Tag's helper commands
  to constrain normal list, read, and search operations.
- **Required service** — local MFS or the Slack bridge. Tag is healthy only when
  MFS answers its health check and the bridge has a live Socket Mode connection
  or a verified hosted receiver connection.
- **Managed process** — a service Tag started and may later stop after validating
  the PID, process start time, and expected command identity.

Historical filenames and environment variables containing `opentag` remain for
compatibility; they do not define a separate product name.

## Architectural decisions

- [Credential boundary](docs/adr/0001-credential-boundary.md)
- [Local process supervision](docs/adr/0002-local-process-supervision.md)
- [Installed application home](docs/adr/0003-installed-home.md)
- [Codex App Server transport](docs/adr/0004-codex-app-server.md)
- [Hosted Slack receiver](docs/adr/0005-hosted-slack-receiver.md)
