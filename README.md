<!-- Modified by klovr.co in 2026 for Tag. See NOTICE and repository history. -->

<p align="center">
  <a href="https://www.hover.team/tag/">
    <img src="assets/branding/tag-icon.png" width="96" height="96" alt="Tag waterdrop icon" />
  </a>
</p>

<h1 align="center">Tag</h1>
<p align="center"><strong>Your personal assistant, in Slack.</strong></p>
<p align="center">
  Turn a team conversation into a checklist, a reviewed PR, or a finished file.<br />
  Bring your own agent. Keep the work in the thread.
</p>
<p align="center">
  <a href="https://www.hover.team/tag/">Meet Tag</a> ·
  <a href="https://www.hover.team/tag/getting-started/">Get started</a> ·
  <a href="https://www.hover.team/tag/capabilities/">Capabilities</a> ·
  <a href="https://github.com/klovr-co/hover-tag/issues">Issues</a>
</p>

[![Tag brings personal assistants into your team's Slack conversations](assets/branding/tag-social-preview.png)](https://www.hover.team/tag/)

## Work already happens in Slack

The context is already there: a question, a few links, a decision buried in a
thread. Tag brings your personal Codex assistant into that conversation to do
the work and reply where everyone can follow along.

- **Turn discussion into action.** Pull together decisions, owners, and deadlines;
  save a checklist or draft a document in your working folder.
- **Bring the missing context.** Find earlier discussions in approved, indexed
  Slack history, or use connected tools to find an email or inspect a repository.
- **Make it yours.** Tag runs on your computer with your agent, files, skills,
  and connected accounts. Your teammates can see the results in Slack.

Only you can invoke your Tag by default. Each teammate can bring their own.
[Learn how personal access works →](https://www.hover.team/tag/access/)

## Bring your Tag to work

Start with a Mac or Linux computer that can stay awake and online, a working
Codex CLI login, and permission to install a Slack app. Agent-guided setup also
requires Node.js and npm.

Install the setup skill:

```bash
npx skills add klovr-co/hover-tag --skill hover-tag-setup -a codex -g
```

Open a new Codex session and ask:

> Use the hover-tag-setup skill to set up Tag for me.

Codex helps check prerequisites and install Tag. Complete Slack authorization
in your own terminal, then bring your Tag online:

```bash
tag start
```

**[Follow the setup guide and try your first Slack task →](https://www.hover.team/tag/getting-started/)**

Prefer a manual install? The same guide covers it. For Windows installation
details and qualification status, see [installation](docs/installation.md).

## More than a reply

| What you want to do | Explore |
| --- | --- |
| Turn attachments and discussions into useful files | [Working with files](https://www.hover.team/tag/workspaces-and-tools/) |
| Find context in conversations and approved sources | [What Tag knows](https://www.hover.team/tag/what-tag-knows/) |
| Bring your tools, skills, and connected accounts | [Adding integrations](https://www.hover.team/tag/adding-integrations/) |
| Find an email and share the relevant details in Slack | [Use Gmail from Slack](https://www.hover.team/tag/use-gmail-from-slack/) |

Tag connects **Slack → your local agent → your files and permitted sources**.
Optional [MFS](https://github.com/zilliztech/mfs) indexing makes approved history
and other sources searchable. Tag does not automatically remember every
conversation. [See how it works →](https://www.hover.team/tag/how-it-works/)

## Early, open source, yours to run

Tag is an alpha project for experimentation in a trusted environment. Codex is
the supported path; Claude Code is experimental. Your agent runs with local
account permissions and inherited bot/MFS credentials; Tag is not a hardened
sandbox. Connected services process requests under their own data policies.
Read the [security model](docs/adr/0001-credential-boundary.md) before connecting
sensitive accounts or files.

[Documentation](https://www.hover.team/tag/) ·
[Troubleshooting](https://www.hover.team/tag/troubleshooting/) ·
[Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md) ·
[Releases](https://github.com/klovr-co/hover-tag/releases) ·
[Security policy](SECURITY.md)

Tag began with the [Open Tag example](https://github.com/zilliztech/mfs/tree/main/examples/open-tag-skill)
from [Zilliz MFS](https://github.com/zilliztech/mfs), inspired by
[Claude Tag](https://www.anthropic.com/news/introducing-claude-tag).
Licensed under [Apache 2.0](LICENSE); see [NOTICE](NOTICE) for attribution.
