# Error reporting for failed Slack requests

Tag's local error-reporting module covers failed backend requests and
unexpected errors while the Slack bridge handles a request. It
gives the requester a reference and recovery actions even when the coding
backend is unavailable. Other failures, such as an attachment exceeding its
size limit, can produce a private error message without a report. Tag stores
the report locally and never sends it to developers automatically.

## What the requester sees

In a channel, Tag shows a reported failure privately to the person who made
the request. In a direct message, it replies in that conversation. The message
contains an evidence-based cause, an eight-character error reference, and
these actions:

| Action | What happens |
| --- | --- |
| **Retry** | Runs the original Slack request again, if it is still available. A new failure gets a new reference linked to the earlier attempt. Review the request before retrying work that may have side effects. |
| **Fix with coding agent** | Shows the requester a short, copyable prompt containing the sanitized report. In a channel, only the requester can see it; in a direct message, it stays in that conversation. Give it to an agent with access to the machine running Tag if you want a local repair. |
| **Report issue** | Opens a private, editable report preview. Review it, add context if useful, then select and copy the text. Submitting the preview returns a private copy; it does not send a report to developers. |

If Tag cannot identify a cause from the available evidence, it says **Cause not
identified**. A timeout means the backend did not finish before a deadline; it
does not establish a network failure. An expired or unavailable reference
cannot reopen a report; run the request again for a fresh one.

## What a report contains

The shareable report is an allowlisted summary: failure time, Tag version,
backend and available backend version, failed stage, cause category, explanation
and evidence, bounded supporting diagnostics and correlated error events, and
any later local memory health-check result with its timestamp. A retry may also
show the earlier reference. Tag classifies explicit backend error codes first,
then recognized evidence for authentication failure, missing executable, rate
limit, idle timeout, maximum runtime, or unexpected backend exit. Otherwise it
uses the unknown category.

The shareable text omits Slack routing IDs, conversation text, file contents,
tokens, and raw logs by default. Tag redacts common credential patterns in
diagnostic text, but review everything you copy, especially context you add
yourself. The preview has a selectable text field because Slack does not offer
a clipboard action there. Long report text is truncated to fit that field.

To contact the developers, paste the reviewed report into
[Hover Community](https://join.slack.com/t/hover-community/shared_invite/zt-4aghkshid-n7fRukS7_J5sR2jDLBXK9A).
Confirmed code bugs are tracked in GitHub Issues. A coding agent without access
to the affected machine can analyze the report but cannot inspect that
installation's logs or settings. The bundled `tag-troubleshoot` skill guides
agents that do have local access.

## Local storage and privacy

Each Tag keeps up to 50 reports for at most 30 days in its private state. The
local record also holds routing IDs so Tag can check who may reopen it; those
IDs are absent from the shareable text. For a normal installation, the
directory is
`~/Tag/NAME/.tag/state/error-reports`; `tag paths --json` identifies the
selected Tag's actual paths. A versioned startup migration creates and verifies
the owner-only directory before starting dependent services. If a report cannot
be saved, the running bridge retains a bounded in-memory copy, which will not
survive a restart.

Report previews are restricted to the original authorized Slack requester and
the original allowed conversation. Local files are protected by filesystem
permissions, but Tag's coding backend runs with the local account's access;
these files are not isolated from that account. Error reporting is separate
from [optional CLI telemetry](telemetry.md), which does not collect raw error
text or these reports.
