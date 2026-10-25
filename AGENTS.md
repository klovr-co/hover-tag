# Repository instructions

## Agent skills

### Issue tracker

Issues and PRDs are tracked in GitHub Issues for `klovr-co/hover-tag`. Feature planning
uses the Tag Features catalog: https://github.com/orgs/klovr-co/projects/3.
Follow its existing feature naming, draft lifecycle, and field conventions.
See `docs/agents/issue-tracker.md` for commands and the authentication fallback
when the integration token cannot access Projects.

### Feature catalog

Proposed product features belong in the
[`Tag Features` GitHub Project](https://github.com/orgs/klovr-co/projects/3/views/1)
and must also be tracked as issues in `klovr-co/hover-tag`. Create the issue, add it to
the project with Status `Idea`, give it the appropriate Area and Tier, set
Verification to `Missing`, and set Version to `TBD`. Keep the issue as the
canonical record for discussion, specification, and implementation history.

### Triage labels

Use the repository's default five-role triage vocabulary. See
`docs/agents/triage-labels.md`.

### Live Slack testing

When asked to test in the sandbox or live in Slack, follow
`.agents/skills/live-slack-test/SKILL.md`. Run it in the main session, not
through an improvised subagent.

### Destructive Slack app operations

Always obtain a fresh, explicit confirmation from the user immediately before
permanently deleting a Slack app. First resolve and display the exact app name,
App ID, workspace name, and Team ID, and state that deletion is irreversible
and recreating the app will produce a different App ID and bot identity. Wait
for the user to confirm that exact target in chat; a previous general request,
an inferred intention, or an agent answering an interactive CLI confirmation
does not count.

Treat resetting local Tag configuration and deleting the remote Slack app as
separate actions. Default to keeping the remote app when resetting or removing
a local workspace connection unless the user separately confirms permanent
remote deletion using the requirements above.

### Pull request release behavior

Every eligible PR merged into `main` automatically publishes an immutable,
numbered alpha or beta release after CI and clean-install checks pass. The
phase comes from `VERSION`. Before merging,
use at most one of these release labels:

- No release label — continue the active prerelease line. For example,
  `v0.2.0-alpha.3` becomes `v0.2.0-alpha.4`, and `v0.2.0-beta.1` becomes
  `v0.2.0-beta.2`. If no numbered prerelease line is active, automation starts
  the release line configured by `VERSION`. While `VERSION` names a published
  stable release, unlabeled merges publish nothing.
- `release:next-patch` — start the next patch line at `alpha.1`. For example,
  `v0.1.0-alpha` becomes `v0.1.1-alpha.1`.
- `release:next-minor` — start the next minor line at `alpha.1` and reset the
  patch component. For example, `v0.1.1-alpha.2` becomes `v0.2.0-alpha.1`.
- `release:skip` — merge without publishing an immutable prerelease. The
  moving `edge` build may still update.

Do not add `release:next-minor` for ordinary alpha increments. Conflicting or
unknown `release:*` labels intentionally prevent publication. Beta lines only
accept `release:skip`; patch and minor labels begin alpha lines. Stable releases
remain manually qualified and published; see `RELEASE.md`.

### Release line targeting

Before merging or recommending a merge, confirm which release the PR targets:
the current `VERSION` line on `main`, also an older stable release (merge to
`main`, then cherry-pick to `release/vX.Y.x`), or a later version (keep it
unmerged as a draft with a `target:vX.Y` label). Never merge a PR labeled for a
later version into `main` before the current line ships stable. Never merge
`main` into a maintenance branch. See "Release lines" in `RELEASE.md`.

### Automatic upgrade migrations

Treat changes required by a new release like database migrations. Ship versioned,
idempotent migrations for existing installations, including configuration,
stored data, Slack app manifests, required permissions, and credential refreshes.
Updating fresh-install defaults or repairing one developer's installation is
not a complete upgrade fix.

Run required migrations automatically during upgrade/startup, before dependent
readiness checks and services. They must work in the background without a TTY,
using existing authorization and preserving unrelated operator settings. Do not
require users to repeat setup or manually edit settings for changes Tag can
apply itself.

Record completion only after verifying the resulting state, including actual
token grants when permissions change. Reload refreshed credentials before
continuing startup. Interrupted or failed migrations must remain safely
retryable; never mark partial work complete. Add regression coverage for older
installations, repeated runs, and failure recovery.

When Slack or another provider requires fresh sign-in or administrator approval,
report the exact remaining action and resume the migration on retry. Existing
confirmation requirements for destructive operations still apply.

### CLI and Tag.app consistency

Whenever the CLI or Tag.app changes, check whether the change should also be
reflected in the other interface. Review affected commands, options, behavior,
output, and setup flows for shared dependencies and corresponding user
experiences. Update the other interface where applicable and validate the
affected flows in both the CLI and Tag.app. If no corresponding change is
needed, briefly explain why in the change summary or PR description.

### Tag.app microanimations

Tag.app animates at the level of its current Microinteractions section in
`desktop/src/styles.css`. Every new or changed control, list, flow step, or
state change must ship with motion at that level, in the same change. Reuse the
`--ease` and `--spring` tokens, and do not add new easing curves. For motion
that CSS cannot do alone, use the helpers in `desktop/src/lib/motion.ts`:
`useFresh` (react to a change), `usePresence` (exit animations),
`useListMotion` (list items enter and leave), and `glide` (sliding selection
indicators).

Cover each of these:

- **Press and hover:** every clickable element gives on press (scale about
  `.96`–`.985`) and fades its hover state. Rows nudge their chevron, and
  avatars react to hover.
- **Appearing and leaving:** menus, details, toasts, and dialogs animate in
  and out, about 120–280 ms. Errors give one small shake.
- **State changes:** switches, checks, radios, completed steps, status colors,
  and progress bars transition instead of snapping. A selection highlight
  glides to the new choice.
- **Life events:** starting a Tag, a new reply, adding or removing a Tag,
  finishing setup, live work, and finishing an update each get a visible
  moment, such as a hop, ripple, confetti, shimmer, or flash.
- **Overflow:** cut-off text that matters, such as a Tag's description,
  scrolls to show the rest only while its row is hovered or focused, then
  eases back. Never autoplay it on every row.

Keep it calm:

- **No flashing:** do not fade whole screens or panels in on mount. Animate
  the contents, not the sky or the frosted panel. Do not replay entrance
  animations each time a screen opens; use `useFresh` so only real changes
  animate.
- **Cheap properties:** animate only `opacity` and `transform`, except for the
  list height animations.
- **No lasting transform on containers:** it breaks `position: fixed` dialogs
  and tooltips inside them. Avoid `animation-fill-mode` on containers.
- **Reduced motion:** `prefers-reduced-motion: reduce` must turn the new
  motion off, including loops, marquees, and hover movement. Helpers must not
  delay unmounting when motion is reduced.
- **Tests:** add tests for new helpers or state-driven motion classes.

### Agent backend parity

Tag supports two agent backends: Codex (`scripts/codex_agent_backend.py`, App
Server) and Claude (`scripts/claude_agent_backend.py`, Agent SDK). Any feature
that depends on the backend must be implemented, tested, and documented for
both in the same change. This includes streaming, activity, approvals,
cancellation, timeouts, retries, model and settings controls, error
classification, setup, sign-in checks, doctor output, and upgrade migrations.

- Keep the Slack bridge backend-neutral. Backends emit the normalized event
  contract described in `references/backends.md`; the bridge must never parse
  backend-native payloads or branch on the backend name for behavior.
- Put shared vocabulary and policy in backend-neutral modules such as
  `scripts/agent_activity.py`, not in one backend's adapter.
- If a provider cannot support a capability, make the limitation explicit in
  code and in `docs/reference/supported-capabilities.md`, and degrade safely
  (fail closed for approvals and permissions). Do not silently ship it for one
  backend only.
- Add regression tests for each backend. Claude tests use a fake
  `claude_agent_sdk` module, so CI does not need the SDK installed.

### Domain docs

This is a single-context repository. Read the root `CONTEXT.md` and relevant
ADRs under `docs/adr/` when they exist. See `docs/agents/domain.md`.

## Documentation presentation

For documentation only: Slack request examples that mention `@Tag` must use
the same Slack-style presentation as **What Tag knows** in the rendered docs.
This includes file-task prompts such as `@Tag read launch-plan.md and create
launch-checklist.md`. Ordinary inline mentions of the product do not need a
Slack illustration.

Keep canonical examples as Markdown blockquotes in this repository. In Hover's
`tag/scripts/sync-docs.mjs`, map them to the existing `SlackPrompt` or
`SlackContextExample` components from `tag/components/slack-example.tsx`.
Reuse the established mention styling, pixel avatars, spacing, and typography.
When showing an illustrative reply, follow the existing once-on-visible
typing-to-reply behavior and reduced-motion support. Animate only the final
message, and only when it is the assistant's reply; show all earlier messages
and human messages immediately. Keep the reply
consistent with the documented capabilities. Do not invent a reply solely to
style a prompt. Never edit generated MDX manually.
