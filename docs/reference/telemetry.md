# Tag telemetry and privacy

Tag contains an optional, privacy-bounded telemetry client that helps Klovr
understand whether people can install, configure, and operate Tag. Approved
release builds use the dedicated destination and collection boundary described
on this page. Source checkouts have no telemetry destination unless they are
packaged by the release workflow.

## Your choice

Before an interactive installation can send its first event, Tag shows a notice
describing the collection boundary. Continuing saves an installation-wide
enabled preference. Turning telemetry off saves a disabled preference, deletes
the local pseudonymous identifier and queued events, and sends no opt-out event.
Non-interactive runs do not choose on the operator's behalf.

You can inspect or change the preference at any time:

```text
tag telemetry status
tag telemetry on
tag telemetry off
```

Set `TAG_TELEMETRY=off` before running Tag for an immediate, process-only hard
stop. While this override is present, Tag creates no telemetry files and makes
no telemetry network requests.

The saved preference applies to every Tag in the same installation and lives at
`$TAG_HOME/config/telemetry.json`, outside versioned release directories.

## Events and properties

Tag can emit only the following fixed events and scalar properties. The client
has no general-purpose event API for prompts, arbitrary labels, or application
data.

| Event | Properties |
| --- | --- |
| `tui_started` | Tag version, OS family, CPU architecture, and whether the invocation is interactive |
| `setup_started` | Setup entry point: `setup`, `add`, or `test` |
| `setup_step_completed` | Fixed setup step and coarse elapsed-time bucket |
| `setup_abandoned` | Last fixed setup step and coarse elapsed-time bucket |
| `setup_completed` | Coarse elapsed-time bucket and backend kind: Codex or Claude |
| `command_completed` | Fixed command group, outcome, and coarse duration bucket |
| `command_failed` | Fixed command group and stable error category |
| `telemetry_preference_changed` | The value `enabled`; disabling sends no event |

Duration values are reduced to `<5s`, `5–30s`, `30–120s`, or `>120s`. Error
categories are fixed values selected by Tag; exception messages and stack
traces are not collected.

Tag never includes Slack messages, channel or workspace identifiers, prompts,
agent input or output, retrieval queries, file contents, source code, paths,
host names, account identities, email addresses, command arguments,
configuration values, environment variables, logs, credentials, tokens, or raw
error text in an event.

## Identifier and local queue

After telemetry is enabled, Tag creates a random installation UUID. It does not
identify a person or create a PostHog person profile. `tag telemetry off`
deletes this identifier.

Events briefly enter a local queue containing at most 64 events. Queued events
expire after 24 hours. Delivery happens in a detached background process;
network failures are silent and cannot change command output or exit status.
The queue is deleted when telemetry is disabled.

## Destination and retention

When approved and activated, events will be sent directly to a dedicated Tag
CLI project in PostHog EU Cloud at `https://eu.i.posthog.com`. They will not be
mixed with Hover website or documentation analytics. Tag disables PostHog
person profiles and IP geolocation and does not use autocapture, session
recording, surveys, feature flags, or error and log capture.

The Tag CLI project uses PostHog's Free plan, which retains event data for one
year. Tag does not copy telemetry into another analytics project or maintain a
separate telemetry export.

For privacy questions, access or deletion requests, contact
[team@klovr.co](mailto:team@klovr.co). The broader Hover website privacy notice
is available at [hover.team/privacy](https://www.hover.team/privacy/).
