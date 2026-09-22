<!-- Modified by klovr.co in 2026 for Tag. See NOTICE and repository history. -->

# Tag

## Your personal assistant, in the workspace you share

Tag brings your Codex agent into Slack. Discuss the work with your teammates,
then mention your Tag to investigate a question, make a plan, or work on files.
It uses the thread as context and replies where everyone can see the result and
follow up.

Your agent still runs on a computer you control, with the workspace, tools,
skills, and connected accounts you make available to it, subject to that local
account's permissions. There is no separate Tag cloud service hosting your
conversations or working files.

[Get started](docs/getting-started/first-task.md) ·
[Read the documentation](docs/index.md) ·
[See what is supported](docs/reference/supported-capabilities.md)

### Ask your agent to set up Tag

Install the setup skill:

```bash
npx skills add klovr-co/hover-tag --skill hover-tag-setup -a codex -g
```

Then open a new Codex session and ask:

```text
Use the hover-tag-setup skill to set up Tag for me.
```

Codex checks the prerequisites and installation, then guides you to `tag setup`
for the Slack login and approval steps that only you or a workspace admin can
complete.

## Work together in the thread

Jules and Maya discuss a launch, then Maya asks their Tag to pull the plan
together:

> **Maya:** I’ll finish the FAQ by Tuesday.
>
> **Jules:** I’ll test signup on Wednesday. Support still needs a briefing.
>
> **Maya:** @Maya's Tag pull this into a launch checklist with owners.
>
> **Maya's Tag:** Here’s the shared checklist:
> - **Maya:** Finish the FAQ by Tuesday.
> - **Jules:** Test signup on Wednesday.
> - **Unassigned:** Brief support before launch. Who can take this?
>
> **Jules:** I'll brief support. We're also missing the go/no-go review. Maya,
> can you own that?

The request and result stay in Slack. Teammates can add context, question an
answer, or take the next step without reconstructing a private AI conversation.

By default, only the owner can ask their Tag to work. Everyone who can see the
channel can read its requests and replies. An owner can deliberately
[share access](docs/concepts/sharing-access.md), but doing so lets another person
request work from the same local agent environment—not merely read the thread.

## What you can delegate

You can ask Tag to:

- summarize a discussion into decisions, owners, and next steps;
- investigate a question across approved Slack history and other indexed
  sources;
- compare information from conversations, documents, issues, and repositories;
- use locally installed tools, skills, and connected accounts available to
  Codex;
- inspect or change files in its configured workspace when explicitly asked;
- return an answer to the thread, post to the current channel, create a Slack
  Canvas, or deliver supported generated images.

What Tag can do depends on its setup. It can use the current thread, files in
its workspace, and the sources and tools made available to it. See
[What Tag knows](docs/concepts/what-tag-knows.md) for examples and limits.

## How Tag works

```text
       ┌──────────────┐
       │    Slack     │    @Maya's Tag <task>
       │              │ ◄──── answer ──────┐
       └──────┬───────┘                    │
              │ thread context             │
              ▼                            │
   ┌────────────────────────────────────┐  │
   │                Tag                 ├──┘
   │          Local Codex agent         │
   └───────────────┬────────────────────┘
                   │ optional retrieval
                   ▼
   ┌────────────────────────────────────┐
   │                MFS                 │
   │ Slack · repos · docs · issues · DB │
   └────────────────────────────────────┘
```

Tag has three parts:

- **Slack** holds the request, up to 30 messages of thread context, and the
  visible result.
- **The agent workspace** is where Codex uses files and locally available tools
  to do the work.
- **MFS** is a searchable context layer over sources the person running Tag has
  indexed and permitted. The agent uses it only when a request needs context
  beyond the current thread and workspace.

Each mention starts a fresh agent run. Continuity comes from context Tag can
reconstruct: the current Slack thread, files that remain in the workspace,
approved indexed sources, and saved user settings. Tag does not quietly build a
permanent memory of every request.

Read [How Tag works](docs/concepts/mental-model.md) for the complete mental
model.

## Get started

Use a Mac or Linux computer that can stay awake and connected while Tag handles
requests. You need:

- Python 3.10 or later;
- `curl`;
- Codex CLI installed, signed in, and able to run tasks;
- permission to create and install a Slack app in your workspace.

The agent-guided setup also needs
[Node.js and npm](https://nodejs.org/en/download) so it can run `npx`. The
direct terminal installer does not require Node.js.

The recommended agent-guided setup is described above. To install directly in
your terminal instead:

```bash
curl -fsSL https://hover.team/tag/install | sh
tag setup
```

Setup helps you create or link a Slack app, select yourself as its owner, choose
channels, and start indexing the approved Slack history. It saves completed
steps, so you can rerun `tag setup` to resume after a pause or approval.

Once setup is complete:

```bash
tag start
tag status
tag stop
```

Keep the host computer awake and connected. Tag runs in the background, but it
does not start automatically after the computer restarts.

For the full walkthrough—including Slack approvals, the first test task, and
recovery—follow [Get started with Tag](docs/getting-started/first-task.md).

## Files and integrations

Each Tag has a user-owned workspace. A normal installation uses
`~/Tag/default` for the default Tag and `~/Tag/NAME` for a named Tag. Run
`tag paths` instead of assuming a location; custom installations can use a
different path.

Files in this workspace remain in place during normal upgrades. The workspace
is the agent's starting directory, not a sandbox: actual file and command
access depends on the backend process and the local account running Tag.

Codex can also use installed commands, skills, and MCP connections. Each tool
or connected service has its own credentials and permissions. Installing a
skill teaches the agent how to use a tool; it does not grant access to an
account.

- [Work with files](docs/concepts/workspaces-and-tools.md)
- [Add integrations](docs/concepts/adding-integrations.md)
- [Use Gmail from Slack](docs/tutorials/use-gmail-from-slack.md)

## Context, access, and security

Tag turns Slack messages and attachments into instructions for a local coding
agent. Treat them as untrusted input.

Important boundaries:

- Only the owner and explicitly authorized Slack members can start work.
- Channel visibility and permission to invoke Tag are separate: anyone who can
  see a channel can read Tag's visible requests and replies.
- Inviting the Slack app to a channel does not make all history searchable.
  Durable retrieval requires an MFS connector and an allowed scope.
- MFS and Slack helper restrictions are application guardrails, not a hardened
  capability boundary. A shell-capable backend runs as the same local account
  and may use credentials inherited by that process.
- Tag is intended for a trusted, isolated environment. It does not create that
  isolation itself; use a dedicated host or local account and least-privilege
  credentials when stronger boundaries matter.

Tag does not provide organization-wide identity policy, auditable approvals,
spend controls, or enterprise administration. Read the
[security policy](SECURITY.md) before connecting sensitive files or accounts.

## Documentation

| I want to… | Read |
| --- | --- |
| Set up Tag and try a first task | [Get started](docs/getting-started/first-task.md) |
| Understand why I’d bring my assistant into Slack | [Why Tag](docs/philosophy/why-tag.md) |
| Know who can use my Tag and see its replies | [Your own Tag](docs/concepts/access.md) |
| Let someone else make requests to my Tag | [Sharing access](docs/concepts/sharing-access.md) |
| See what happens after a mention | [How Tag works](docs/concepts/mental-model.md) |
| Follow up or find an earlier discussion | [What Tag knows](docs/concepts/what-tag-knows.md) |
| Work with attachments and saved files | [Working with files](docs/concepts/workspaces-and-tools.md) |
| Add tools, skills, and connected accounts | [Adding integrations](docs/concepts/adding-integrations.md) |
| Check the exact current feature set | [Supported capabilities](docs/reference/supported-capabilities.md) |
| Configure and operate Tag | [Tag management](docs/tag-management.md) |
| Diagnose a problem | [Troubleshooting](docs/troubleshooting.md) |

Implementation-level references cover the
[Slack adapter](references/slack-adapter.md),
[backend behavior](references/backends.md),
[runtime agent contract](references/runtime-agent.md), and
[memory model](references/memory.md).

## Project status

Tag is an early open-source project built from a workflow already used in
day-to-day Slack discussions. Codex is the supported path; other backends remain
experimental. Tag is ready for experimentation in a trusted environment, but
it is not a production security boundary.

Issues and contributions are welcome. Maintainers can run the same validation
used by GitHub Actions from a source checkout:

```bash
git clone https://github.com/klovr-co/hover-tag.git
cd hover-tag
./install.sh --dependencies-only
./scripts/ci_check.sh
```

See the [release contract](RELEASE.md) and [changelog](CHANGELOG.md) for release
details.

## Origins and attribution

Tag is an open-source reference implementation inspired by
[Claude Tag](https://www.anthropic.com/news/introducing-claude-tag). It began as
a modified derivative of the
[Open Tag Example](https://github.com/zilliztech/mfs/tree/main/examples/open-tag-skill)
from [Zilliz MFS](https://github.com/zilliztech/mfs). The upstream material is
licensed under the Apache License 2.0. This repository contains subsequent
modifications and extensions.
