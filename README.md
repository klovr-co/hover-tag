<!-- Modified by klovr.co in 2026 for Tag. See NOTICE and repository history. -->

<p align="center">
  <a href="https://www.hover.team/tag/">
    <img src="https://raw.githubusercontent.com/klovr-co/hover-tag/main/assets/branding/tag-team-transparent.png" alt="Jules, Iris, Maya, Rowan, and Zara, each with their own Tag" />
  </a>
</p>

<h1 align="center">Tag</h1>
<p align="center">by <a href="https://www.hover.team/">Hover</a></p>
<p align="center"><strong>Multiplayer AI, right in Slack.</strong></p>
<p align="center">
  Powered by your Codex or Claude Code setup · Runs on a computer you control
</p>
<p align="center"><strong>Everyone brings their own Tag. Everyone works in the same conversation.</strong></p>
<p align="center">
  <strong><a href="https://www.hover.team/tag/"><img src="https://raw.githubusercontent.com/klovr-co/hover-tag/main/assets/branding/tag-cutout.png" width="28" height="28" alt="" /> Read the Tag guide at hover.team/tag →</a></strong>
</p>
<p align="center">
  <a href="https://www.hover.team/tag/getting-started/">Get started</a> ·
  <a href="https://join.slack.com/t/hover-community/shared_invite/zt-4aghkshid-n7fRukS7_J5sR2jDLBXK9A"><img src="https://raw.githubusercontent.com/klovr-co/hover-tag/main/assets/branding/slack-icon.svg" width="16" height="16" alt="Slack" /> Join the Slack community</a> ·
  <a href="https://www.hover.team/tag/capabilities/">Capabilities</a> ·
  <a href="https://github.com/klovr-co/hover-tag/issues">Issues</a>
</p>

## Work through it together

The context is already there: a question, a few links, a decision buried in a
thread. Mention your Tag where the discussion is happening. Teammates can add a
missing detail, question the answer, or take on the next step. Everyone can
follow how you got there.

- **Turn discussion into action.** Pull together decisions, owners, and deadlines;
  save a checklist or draft a document in your working folder.
- **Bring the missing context.** Find earlier discussions in approved, indexed
  Slack history, or use connected tools to find an email or inspect a repository.
- **Make it yours.** Tag runs on a computer you control using your Codex or Claude Code
  setup, files, skills, and connected accounts. Your teammates can see the results in
  Slack.

Your Tag runs from your own agent environment, and only you can invoke it by
default. Teammates can bring their own Tag with their own setup. You share the
conversation and results, and you decide who can ask your Tag.
[Learn how access works →](https://www.hover.team/tag/access/)

## Runs locally, with clear boundaries

Tag's Slack bridge and your Codex or Claude Code agent run on a computer you control, using the
files, tools, skills, and accounts you choose to make available.

- **Hover does not host your conversations or working files.** Your agent
  workspace and the files Tag creates remain on your computer.
- **Your files stay useful outside Tag.** They are ordinary files in your working
  folder, so you can open, edit, move, or reuse them with other tools.
- **External services are still external.** Slack carries the team conversation;
  your AI provider, such as OpenAI, Anthropic, or an API gateway you choose, processes agent
  requests; and optional connected services process the
  information required to use them, each under its own data policies.

Tag is local by design, not offline.
[See how Tag works →](https://www.hover.team/tag/how-it-works/)

## Bring your Tag to work

Start with a Mac, Windows, or Linux computer that can stay awake and online, an
AI connection (Codex, Claude Code, a ChatGPT plan, or an API key), and
permission to install a Slack app.

**[Download Tag](https://www.hover.team/tag/)** for macOS, or get it from
[GitHub releases](https://github.com/klovr-co/hover-tag/releases). The app for
Windows and Linux is coming soon; until then, use the terminal installer below.
On first run it installs Tag, then guides you through naming your Tag,
choosing its AI model, connecting Slack, and picking channels. Your Tag starts
when setup finishes.

Prefer a terminal? Install the `tag` command, set up, and bring your Tag online:

```bash
curl -fsSL https://hover.team/tag/install | sh
tag setup
tag start
tag status
tag stop
```

Or let your coding agent do it. Install the setup skill (needs Node.js and npm):

```bash
# Codex
npx skills add klovr-co/hover-tag --skill hover-tag-setup -a codex -g
# Claude Code
npx skills add klovr-co/hover-tag --skill hover-tag-setup -a claude-code -g
```

Then open a new session and ask:

> Use the hover-tag-setup skill to set up Tag for me.

**[Follow the setup guide and try your first Slack task →](https://www.hover.team/tag/getting-started/)**

From a source checkout, run `./install.sh` (Windows: `./install.ps1`). See
[installation](docs/installation.md) for release channels, upgrades from v0.2,
and uninstalling, and [installation details](docs/reference/installation-details.md)
for terminal and source installs.

## More than a reply

| What you want to do | Explore |
| --- | --- |
| Turn attachments and discussions into useful files | [Working with files](https://www.hover.team/tag/workspaces-and-tools/) |
| Find context in conversations and approved sources | [What Tag knows](https://www.hover.team/tag/what-tag-knows/) |
| Bring your tools, skills, and connected accounts | [Adding integrations](https://www.hover.team/tag/adding-integrations/) |
| Find an email and share the relevant details in Slack | [Use Gmail from Slack](https://www.hover.team/tag/use-gmail-from-slack/) |

Tag connects **Slack → your local agent → your files and permitted sources**.
Optional [MFS](https://github.com/zilliztech/mfs) indexing makes approved history
and other sources searchable. Tag remembers facts you explicitly ask it to
remember, but not every conversation. [See how it works →](https://www.hover.team/tag/how-it-works/)

## Early, open source, yours to run

Tag is an early project for experimentation in a trusted environment. Codex and
Claude Code are both supported, as are other AI providers through compatible APIs
and gateways; each Tag has one model, chosen in the Tag app or the
CLI. Your agent runs with local
account permissions and inherited bot/MFS credentials; Tag is not a hardened
sandbox. Connected services process requests under their own data policies.
Read the [security model](docs/adr/0001-credential-boundary.md) before connecting
sensitive accounts or files.

Tag includes privacy-bounded telemetry for setup and reliability in the CLI and
the desktop app. It never collects prompts, Slack messages, agent output, workspace
paths, files, source code, logs, credentials, configuration values, or command
arguments. Use **Share usage data** in the app's Settings → **General**, or `tag telemetry status`,
`tag telemetry on`, or `tag telemetry off`, to manage it, or set
`TAG_TELEMETRY=off` for an immediate process-level stop. Read the full
[telemetry and privacy reference](docs/reference/telemetry.md).

[Documentation](https://www.hover.team/tag/) ·
<a href="https://join.slack.com/t/hover-community/shared_invite/zt-4aghkshid-n7fRukS7_J5sR2jDLBXK9A"><img src="https://raw.githubusercontent.com/klovr-co/hover-tag/main/assets/branding/slack-icon.svg" width="16" height="16" alt="Slack" /> Join the Slack community</a> ·
[Troubleshooting](https://www.hover.team/tag/troubleshooting/) ·
[Source](https://github.com/klovr-co/hover-tag.git) ·
[Contributing](https://github.com/klovr-co/hover-tag/blob/main/CONTRIBUTING.md) · [Changelog](https://github.com/klovr-co/hover-tag/blob/main/CHANGELOG.md) ·
[Releases](https://github.com/klovr-co/hover-tag/releases) ·
[Security policy](SECURITY.md)

Tag began with the [Open Tag example](https://github.com/zilliztech/mfs/tree/main/examples/open-tag-skill)
from [Zilliz MFS](https://github.com/zilliztech/mfs), inspired by
[Claude Tag](https://www.anthropic.com/news/introducing-claude-tag).
Licensed under the [Apache License 2.0](LICENSE); see [NOTICE](NOTICE) for
attribution.
