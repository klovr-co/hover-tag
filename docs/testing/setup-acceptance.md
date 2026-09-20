# Setup acceptance scenarios

Feature: [Guided setup and management — #13](https://github.com/klovr-co/tag/issues/13).

These are BDD-style requirements, not executable Cucumber tests. Test the eight
main journeys first, then the two live security checks. Results belong in
[setup-test-results.md](setup-test-results.md), not inside the scenarios.

## Test surfaces and safety

- Local prototype: `.context/setup-workflow.html` (gitignored; not a published
  artifact). Its buttons simulate authorization, app creation, indexing and replies.
- Browser DOM smoke checks: [setup-prototype-checks.js](setup-prototype-checks.js).
  They test handlers, not real Slack access or human usability.
- Live CLI: use a dedicated test installation and Slack sandbox, only after
  explicit approval for app creation, permission changes, indexing and messages.
- Never put tokens in this checklist, screenshots, issue comments, or agent chat.
- Use two synthetic channels with distinct test messages, an owner, a second
  caller, and operators with/without app-management access for live testing.
- Owner-only invocation does not make replies private: channel members can see them.
- Per ADR 0001, the backend inherits credentials. Allowlist tests verify normal
  application behavior, not hardened isolation from a malicious backend.
- Slack-only memory is the agreed onboarding design. These scenarios do not
  assert that the existing multi-source runtime has already been restricted.

## F01 — New app and first reply

```gherkin
Given I have authorized Slack and can install an app in the selected workspace
When I choose "Create a new Tag app" and approve creation
And select one or more channels and confirm the displayed defaults
Then setup prepares required tools and discloses invitation-following memory
And after approval indexes joined channels within the configured history window
And uses Codex, owner-only invocation, and a 30-day history window by default
And reports service connection separately from a verified first reply
When I mention the selected bot and its reply is observed
Then setup records the first reply as verified
```

Prototype route: Start → Connect Slack → simulated authorization → workspace →
new app → creation approved → channel → Use channel → wait → simulated reply.

## F02 — Existing app

```gherkin
Given I can manage TeamHelper in the selected workspace
When I select TeamHelper
Then Tag checks its compatibility without silently modifying it
And requests approval for any required configuration changes
When I confirm its settings and select a channel
Then TeamHelper and that channel stay selected through setup and the test mention
```

Route: Start → Connect Slack → authorization → workspace → TeamHelper → settings
confirmed → select support → Use support. Check the app name at every stage.

## F03 — Repair missing settings

```gherkin
Given an existing app lacks required permissions or event configuration
When its checks fail
Then setup explains the specific missing requirements
And preserves unrelated app settings and completed setup
When I approve and complete the repair and checks succeed
Then setup continues with the same app without creating a duplicate
```

Route: app picker → TeamHelper → missing permissions → check again → settings
confirmed. Live verification must include invalid/expired tokens, mismatched
app credentials, missing scopes and missing app_mention subscriptions; the
prototype only illustrates generic guidance.

## F04 — Pause and resume

```gherkin
Given setup is waiting for agent sign-in with my app and channel already selected
When I save and exit, then resume
Then I return to the blocked step with completed settings preserved
When sign-in is verified
Then setup continues without requesting saved Slack credentials again
```

Route: select app/channel → preview toolbar "Needs attention" → Save and exit →
resume → Check again. The prototype retains state only in the open page.
Live variant: exit the process, relaunch `tag setup`, and verify durable resume.

## F05 — Optional defaults

```gherkin
Given I selected an app and channel
When I change the history window to 7 days and choose Claude
Then Claude is identified as experimental
And the app, channel and owner-only access remain unchanged
And reopening defaults and continuing setup preserves those choices
```

Route: channel → Change defaults → 7 days / Claude → Save → reopen defaults →
Save → Use channel. Also run with untouched Codex defaults.

## F06 — Authorized app manager

```gherkin
Given my account can manage app A but cannot manage app B
When I choose a workspace
Then the picker offers only apps I am authorized to manage
And selecting an app does not modify its configuration
And any required changes need explicit approval
```

Requires permission-aware fixtures or live accounts. A static sample list is
not evidence. If discovery is unsupported, explain it and offer manual connection.

## F07 — Administrator approval and replay

```gherkin
Given installing the new app requires workspace administrator approval
When I request installation
Then setup waits without reporting the app as connected
And I can save and resume the wait
When approval is confirmed
Then setup resumes the correct app setup step
When I subsequently start a fresh setup and authorize Slack
Then I choose the workspace and app again
And stale approval state cannot skip those choices
```

Route: new app → approval required → Save and exit → resume → approval received.
Replay variant: then click toolbar Start → Connect Slack → authorization completed.
Expected next screen: workspace picker, not channel selection.

## F08 — Insufficient app or channel access

```gherkin
Given I cannot manage an app or the selected bot cannot access a channel
When I try to configure that destination
Then setup explains the missing access and offers a permitted alternative
And does not reveal inaccessible channel content or silently broaden permissions
And does not report the connection as verified
```

Cover no manageable apps, discovery unavailable, no joined channels, private
channel denied, and access revoked after selection. Requires restricted fixtures;
these screens are not currently in the prototype.

## S01 — Unauthorized caller (live)

```gherkin
Given only the owner is allowed to invoke Tag
When a different member mentions the bot
Then Tag denies the request before retrieving thread context or invoking a backend
And the configured owner can still complete an allowed task
```

Record sanitized instrumentation proving no thread-read, memory retrieval or
backend invocation for the denied request. A denial message alone is insufficient.

## S02 — Slack memory scope (live)

For selected-channel policy, use the scenario below. For invitation-following
policy, channel B must not be joined by the bot; invited channels are explicitly
in scope under the updated product policy (I01).

```gherkin
Given only channel A and its approved history window are configured for memory
And channel B contains a unique synthetic test message
When the Slack connector indexes and Tag searches its permitted memory
Then channel A's in-window test message is retrievable
And channel B and out-of-window messages are not indexed by this setup
And helper requests outside the configured scope are rejected
And no local folders or repositories are indexed
```

Inspect connector allowlists, indexed records, and helper rejection evidence—not
just an empty search result. This does not prove credentials cannot bypass helpers
(ADR 0001). A stronger isolation requirement needs a separate architecture decision.

## M01 — Multiple channels and selection validation

```gherkin
Given I am choosing channels for the selected app
When I select team and product
Then both are checked and the action says "Use 2 channels"
And both stay selected when I change defaults or resume setup
When I deselect every channel
Then I cannot continue until at least one channel is selected
When I continue with only support selected
Then removed channels are absent from the setup summary
And the first-mention instructions let me use any selected channel
```

## S03 — No cross-channel memory disclosure (live; proposed policy)

```gherkin
Given channels A and B are both selected for indexing
And B contains a unique synthetic private message
When an allowed caller asks a question in A
Then normal retrieval is restricted to A's context
And the reply does not disclose B's indexed message
```

Verify the retrieval filter and query evidence, not just answer text. This is a
new runtime requirement, not implemented by the prototype, and is still subject
to ADR 0001's trusted-backend limitation. Cross-channel sharing requires a
separate explicit policy; selecting multiple channels must not enable it.

## L01 — Link an existing app by ID

```gherkin
Given I chose a workspace
When I choose "Link an existing app by ID"
Then I can open Slack's Your Apps page in a new tab
And I am guided to Basic Information to copy the App ID
And malformed IDs cannot advance
When I submit a valid-format App ID
Then the prototype illustrates checks without sending a Slack request
And a direct settings link targets that App ID
And selecting a different app cannot retain the old settings link
```

Live implementation must verify app access and compatibility separately from
ID format validation. No automatic permission changes are authorized by linking.

## I01 — Invitations automatically enable channel memory (live)

```gherkin
Given invitation-following memory was approved and Tag is running
And channel A is joined and channel B is not joined
When the app is invited to public or private channel C
Then the next membership reconciliation enables C without another channel picker
And validates the separate history credential before requesting indexing
And indexes within the configured history window, without including B
And keeps replies restricted to their current channel's memory
When the app leaves C
Then the next reconciliation removes normal reply/retrieval access to C
And does not claim previously indexed C data was erased
When membership, history access, or index submission fails
Then invitation memory reports needs attention and retries without terminal input
And neither service readiness nor sync submission is called a verified reply
```

Reconciliation normally runs about every minute; sync requests on unchanged
membership are spaced about five minutes apart. Slow API/index operations may
delay this. Existing completed setups retain selected policy unless opted in.
No-public-channel cases must include private-only membership and an empty list.
Current helper restrictions are not a hardened security boundary (ADR 0001).

## Recording a run

Use: scenario ID, tester, date, artifact hash/commit, test surface, starting role,
actual result, expected result, status, evidence, defect ID, and retest reference.
Statuses: Not tested / Pass / Fail / Blocked / Not applicable / Needs retest.
Record human acceptance separately from automated checks. Fixes move a failed
case to Needs retest; never overwrite the original failure with an unverified Pass.
