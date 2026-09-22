# How Tag works

Here is the basic model:

> Slack is where your team delegates. The agent does the work. MFS supplies
> permitted context when the task needs it.

## The three parts

| Part | What it does | What the operator chooses |
| --- | --- | --- |
| **Slack** | Holds the request, thread context, working state, and result | App installation, authorized callers, and selected channels |
| **Agent workspace** | Runs Codex or Claude and gives the agent files and locally available tools | Backend, working directory, local tools, and process permissions |
| **MFS** | Acts as a searchable context layer over indexed sources | Connectors, credentials, indexed sources, and allowed retrieval scopes |

Slack is the shared interface, not the place where commands run. The CLI agent
does the work, but it does not automatically know the organization's history.
MFS gives it searchable context without opening every indexed source to Tag.

## What happens after a mention

```text
1. You mention your Tag in Slack.
2. Tag reads the current thread and supported attachments.
3. Tag starts a CLI agent in the configured workspace.
4. The agent uses thread context to understand the request.
5. If more context is needed, it searches permitted MFS scopes.
6. The agent performs the requested analysis or workspace work.
7. Tag returns the result to the Slack thread.
8. The team follows up in the same conversation.
```

One Slack API page containing up to 30 messages from the current thread is
passed to each run, and the triggering request is passed separately. You can
ask your Tag to "compare that with the current plan" without restating the
discussion. Only the owner can request work from their Tag. Teammates can add
details in the thread for the owner's next request to draw on.
Anything outside that slice of the thread must come from the workspace, an
approved MFS source, or a tool available to the agent.

## What persists

| Information | Persists where | Available on a later mention? |
| --- | --- | --- |
| Current conversation | Slack thread | Yes, within the page of up to 30 messages that Tag fetches |
| Files and task output | Configured workspace | Yes, while they remain in that workspace |
| Indexed organizational context | MFS | Yes, while the source remains indexed and permitted |
| Model reasoning process | Current CLI run | No |
| Your Codex model, reasoning, and Fast Mode choices | Tag's local runtime state | Yes, for your requests across channels and threads in that Tag |

Each mention starts a fresh agent run. Tag does not reuse the previous
model's private reasoning state. A follow-up works because Tag can read the
thread again and because the workspace, indexed sources, and saved user settings
are still there.

## When Tag uses MFS

Tag does not search MFS for every request. The current thread may already have
the answer, or the task may only involve files in the workspace.

When outside context is needed, the agent searches only roots listed in
`MFS_ALLOWED_SCOPES`. Search results can then be reopened for more precise
evidence. A source must be both indexed in MFS and permitted to this Tag
deployment before the agent can retrieve it through the bundled helpers.

## What operators control

The operator configures:

- who can invoke Tag;
- the Slack channels where Tag may run;
- whether Codex or Claude performs the work;
- the workspace in which the agent runs;
- the MFS sources and roots available for retrieval;
- credentials and grants used by connectors and local tools;
- timeouts, retries, and available Codex model settings.

For the exact current feature set, see
[Supported capabilities](../reference/supported-capabilities.md). For the
implementation-level behavior contract, see the
[runtime agent reference](../../references/runtime-agent.md).
