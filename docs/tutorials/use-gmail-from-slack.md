# Use Gmail from Slack

Ask Tag to help set up Gmail access, then find an email about a launch.
This tutorial uses Google Workspace CLI (`gws`); an MCP connection is not needed.

This guide uses Codex and assumes you have completed
[your first Tag task](../getting-started/first-task.md).
See [Adding integrations](../concepts/adding-integrations.md)
for how Tag uses commands, skills, and tool configuration.

## 1. Ask Tag to set up Gmail

Open a DM with your Tag and send this request. You do not need to mention it
in a DM. The example reply shows how Tag might hand the login step back to you:

> Help me set up Gmail access using https://github.com/googleworkspace/cli.
>
> Check what's already installed and authenticated, install any missing tools
> and the gws-shared and gws-gmail skills for Codex in your workspace, and
> guide me through any login steps I need to complete.
>
> **Zara's Tag:** If gws isn't authenticated yet, complete its login on the machine
> running Tag. Once you're signed in, ask me to check Gmail access.

Tag can attempt the setup using its available tools and permissions. If an
installation needs approval or an interactive terminal, complete that step
on the machine running Tag, following the
[Google Workspace CLI installation guide](https://github.com/googleworkspace/cli#installation).

## 2. Complete authentication

If `gws` already has working Gmail access, reuse it. Otherwise, follow the
[Google Workspace CLI authentication guide](https://github.com/googleworkspace/cli#authentication)
and the setup instructions Tag provides. A Google browser login alone does
not configure `gws`: it also needs OAuth credentials and consent for Gmail.

Complete any browser login or local terminal steps on the machine running Tag.
Then return to the DM and ask Tag to check Gmail access. Keep passwords and
credential files out of the conversation.

If setup changed the command search path or environment, run `tag restart`
from the terminal with those changes before continuing.

## 3. Find an email

In a channel where Tag is available, mention it and ask. Use your own Tag's
mention in place of `@Zara's Tag`. The replies below are illustrative; Tag's
answer will depend on your email and request.

> @Zara's Tag use gws to find the latest email about the launch schedule. Summarize
> what changed and include the sender, subject, and date.
>
> **Zara's Tag:** The launch moved from Friday to Monday so support has time to prepare.
> The FAQ and signup testing deadlines are unchanged.
>
> From: Jules. Subject: Updated launch schedule. Date: September 17, 2026.

Use a topic you know is in your mailbox, then compare the answer with the email.
If there is no match, try a more specific subject or sender.

## 4. Write an email

In the same thread, ask Tag to draft a reply. Include the details you want it
to use:

> @Zara's Tag draft a reply to Jules acknowledging the Monday launch.
> Say I'll brief support on Thursday. Show me the draft here before sending.
>
> **Zara's Tag:** Here's a draft for review:
>
> Subject: Re: Updated launch schedule
>
> Hi Jules,
>
> Thanks for the update. Monday works for me. I'll brief support on Thursday
> so they're ready for launch.
>
> Thanks, Zara
>
> This is a draft in Slack; I haven't sent it.

Review the recipient, dates, and wording, then ask for any changes in the same
thread. This step writes the draft in Slack; it does not create a Gmail draft
or send an email.

## If something is missing

| Symptom | What to check |
| --- | --- |
| Tag cannot find `gws` | Run `gws --version` in the terminal you use to start Tag; restart after changing the environment. |
| Codex cannot find the Gmail skill | Check the workspace's `.agents/skills` directory and each skill's `SKILL.md`, then send a new request. |
| Gmail returns an authorization error | Follow the [authentication guide](https://github.com/googleworkspace/cli#authentication) to resolve the `gws` login or scope error, then ask Tag to check Gmail access again. |
| Gmail works in your terminal but fails through Tag | Check that Tag runs under the same local account with the same tool configuration and required environment variables. |

`tag doctor` checks Tag's configuration and core services. The Gmail request
in step 3 checks the additional tool you have installed.

Learn more about commands, supported services, and agent skills in the
[Google Workspace CLI GitHub repository](https://github.com/googleworkspace/cli).
