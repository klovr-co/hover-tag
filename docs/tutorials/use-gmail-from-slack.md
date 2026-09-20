# Use Gmail from Slack

This tutorial uses your existing Google login to find an email about a launch.
It uses the `gws` command directly; an MCP connection is not needed.

This guide uses Codex and assumes you have completed
[your first Tag task](../getting-started/first-task.md). The terminal examples
use macOS or Linux. See [Adding integrations](../concepts/adding-integrations.md)
for how Tag uses commands, skills, and tool configuration.

## 1. Check Google Workspace CLI

On the machine running Tag, open a terminal and run:

```sh
gws --version
```

If it is not installed, one option is:

```sh
npm install -g @googleworkspace/cli
```

This option requires Node.js and npm. Other installation methods are in the
[Google Workspace CLI guide](https://github.com/googleworkspace/cli#installation).

### 2. Check Gmail access

If Gmail already works through `gws`, keep your existing login. Otherwise,
follow its [authentication guide](https://github.com/googleworkspace/cli#authentication)
to configure the Google Cloud OAuth client, then log in with Gmail selected:

```sh
gws auth login -s gmail
```

Check access with a small read request:

```sh
gws gmail users messages list --params '{"userId":"me","maxResults":1}'
```

A successful response confirms the request worked; an empty mailbox may have
no messages to return.

### 3. Add the Gmail skills

If Codex already has the Gmail skills available, skip this step. Otherwise,
change to the workspace path reported by `tag paths` and run:

```sh
npx skills add https://github.com/googleworkspace/cli/tree/main/skills/gws-shared
npx skills add https://github.com/googleworkspace/cli/tree/main/skills/gws-gmail
```

In the installer, choose Codex and installation in the current project.
Check that `.agents/skills` contains `gws-shared` and `gws-gmail`, each with a
`SKILL.md` file. The shared skill supplies common command and authentication
instructions. See the upstream
[skills guide](https://github.com/googleworkspace/cli#ai-agent-skills) for more.

### 4. Ask from Slack

If you changed your terminal's command search path or environment, restart Tag
from that terminal so the running service receives the changes:

```sh
tag restart
```

Use your own Tag's mention in place of `@Maya's Tag`. Then ask:

> @Maya's Tag use gws to find the latest email about the launch schedule. Summarize
> what changed and include the sender, subject, and date.

Use a topic you know is in your mailbox, then compare the answer with the email.
If there is no match, try a more specific subject or sender. If you saved open questions in the
[attachment example](../concepts/workspaces-and-tools.md#turn-a-brief-into-questions-you-can-resolve),
you can also ask Tag to update that file:

> @Maya's Tag use that email to update the open questions you saved from my brief.
> Mark any questions it answers and keep the unresolved ones.

### If something is missing

| Symptom | What to check |
| --- | --- |
| Tag cannot find `gws` | Run `gws --version` in the terminal you use to start Tag; restart after changing the environment. |
| Codex cannot find the Gmail skill | Check the workspace's `.agents/skills` directory and each skill's `SKILL.md`, then try a new mention. |
| Gmail returns an authorization error | Try the read request above in your terminal and resolve the `gws` login or scope error there. |
| Gmail works in your terminal but fails through Tag | Check that Tag runs under the same local account with the same tool configuration and required environment variables. |

`tag doctor` checks Tag's configuration and core services. The Gmail read
request and Slack task above check the additional tool you have installed.
