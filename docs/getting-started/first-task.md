# Get started with Tag

Connect Tag to Slack, then try a task in a thread. You can set up with help
from Codex or run the installer yourself.

## Before you begin

Use a Mac or Linux computer that can stay awake and connected to the internet
while Tag handles requests. You'll need:

- Codex CLI installed, signed in, and able to run tasks on that computer.
- Permission to create and install a Slack app in your workspace. Your workplace
  may require an administrator to approve it.

By default, only you can ask your Tag to work. Other people in the channel can
read your requests and Tag's replies. See [Your own Tag](../concepts/access.md)
for how Tag uses your agent's files, tools, and connected accounts.

Claude support is coming soon.

## Set up Tag

Choose one setup method. Both use the same installer and Slack setup flow.

### Set up with Codex

Install the setup skill from your terminal. This command requires
[Node.js and npm](https://nodejs.org/en/download):

```bash
npx skills add klovr-co/hover-tag --skill hover-tag-setup -a codex -g
```

Open a new Codex session and ask:

```text
Use the hover-tag-setup skill to set up Tag for me.
```

Codex checks what's already installed, helps with missing prerequisites,
and installs Tag if needed. It then asks you to run `tag setup` in your own
terminal to authorize Slack and choose your app and channels.

Use your own Slack account as the owner. Review the channel list, history
window, and invitation policy before finishing. New setups include channels
the app has already joined; later invitations also make channels eligible for
replies and history indexing.

Complete the prompts in your terminal. If credentials need manual entry, enter
them there, not in your Codex conversation.

Return to Codex when setup finishes or pauses. It can check the connection or
help diagnose the failed step. Once Tag is connected, continue to
[Try your Tag in Slack](#try-your-tag-in-slack) below.

### Set up in your terminal

Have [Python 3.10 or later](https://www.python.org/downloads/),
[`curl`](https://curl.se/download.html), and
[Slack CLI](https://docs.slack.dev/tools/slack-cli/) installed.
Tag uses [`uv`](https://docs.astral.sh/uv/getting-started/installation/) if
available, or Python's venv and pip otherwise.

Run the installer:

```bash
curl -fsSL https://hover.team/tag/install | sh
```

It creates a persistent Tag home, a workspace folder for your files, and the
`tag` command. If your terminal cannot find `tag`, add its default command
directory to this terminal's path:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Add the same line to your shell configuration (`~/.zshrc` for zsh or
`~/.bashrc` for bash) to keep it available in new terminals.

Start guided setup:

```bash
tag setup
```

Follow the prompts to:

1. Authorize Slack CLI for your workspace.
2. Create a new Slack app for your Tag, or link an existing app you manage.
   Setup attempts to connect credentials automatically; hidden token entry is
   a recovery option.
3. Select your own Slack account as the owner and choose channels.
   Private channels need an invitation before they appear.
4. Review the channels, history window, and invitation policy. New setups
   include channels the app has already joined; later invitations also make
   channels eligible for replies and history indexing.
5. Finish setup to start Tag and begin indexing the approved Slack history.

Setup saves completed answers. If you pause or encounter an error, run
`tag setup` again to resume.

For detailed setup options, see [Set up and manage Tag](../tag-management.md).

## Start Tag

Once setup is saved, this is the command to bring Tag online:

```bash
tag start
```

Tag runs in the background, so you can close this terminal. Keep the computer
awake and connected. After restarting your computer, run `tag start` again;
you don't need to repeat setup.

If guided setup already started Tag, you're ready to continue. You can check
the connection anytime with `tag status`.

Try the task below to check that your Tag answers in Slack.

## Try your Tag in Slack

In a channel connected during setup, post a short planning note. You can use
the example below or write one for your own work.

Reply in that message's thread and mention your Tag. These examples use
Maya's Tag; select your own app's mention from Slack's suggestions. The replies
below are illustrative. Continue in the same thread to assign the support
briefing and update the checklist:

> **Maya:** We need to prepare the launch. Maya will finish the FAQ by Tuesday.
> Jules will test signup by Wednesday. Nobody has taken the support briefing yet.
>
> **Maya:** @Maya's Tag turn this into a checklist with owners and deadlines. Flag anything missing.
>
> **Maya's Tag:** Here's the launch checklist:
>
> ☐ Finish the FAQ — Maya, by Tuesday.
>
> ☐ Test signup — Jules, by Wednesday.
>
> ☐ Brief support — owner and deadline missing. Who will take this, and by when?
>
> **Maya:** @Maya's Tag I'll handle the support briefing on Thursday. Update the checklist.
>
> **Maya's Tag:** Updated the checklist:
>
> ☐ Finish the FAQ — Maya, by Tuesday.
>
> ☐ Test signup — Jules, by Wednesday.
>
> ☐ Brief support — Maya, on Thursday.
>
> All three tasks now have an owner and a deadline.

Check that the first reply appears in the same thread, keeps the two assigned
tasks and their deadlines, and flags the missing owner and deadline for the
support briefing. That confirms Tag received your request, ran Codex, and
returned a result to Slack.

The updated checklist should include your follow-up alongside the earlier
tasks. Tag uses the thread messages as context.

Once this works, explore [Working with files](../concepts/workspaces-and-tools.md)
or [Adding integrations](../concepts/adding-integrations.md).

## Need help?

Run `tag doctor` to check for problems, then follow
[Troubleshooting](../troubleshooting.md). Stop Tag at any time with `tag stop`.
