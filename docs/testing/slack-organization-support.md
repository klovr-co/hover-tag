# Slack organization support — issue #148

## Implementation and automated evidence

Checked on 2026-10-02 with Python 3.12 and Slack Bolt 1.30.0.

- Full repository suite: 809 tests, successful, one skip.
- Following the final existing-grant and HTTP-method changes: all 88 focused
  organization, channel, credential, migration, and search-scope tests passed.
- Policy gate: release metadata, documentation, secret/state, Python compilation,
  shell syntax, Slack manifest, and whitespace checks passed.

Coverage includes colored/mixed organization and workspace account listings,
workspace selection and pause, saved organization/workspace identities,
interrupted app creation, reuse of the saved App ID, explicit initial grants,
preservation of existing organization grants, credential handoff, paginated
workspace-grant verification, mismatched app/organization/workspace rejection,
and retryable migration checkpoints. Real Bolt dispatch tests include an
organization authorization with a null Team ID and reject another receiving
workspace before any handler runs. MFS tests check separate channel caches for
workspaces using the same organization token and deny missing or mismatched
grants. Search tests retain the selected workspace's scopes in a shared database.

The first full run found a missing release-package entry, which was fixed.
A timing-sensitive, unrelated large-image App Server test initially timed out;
it passed both its focused rerun and the subsequent full suite.

## Separate live sandbox acceptance — pending

The operator deferred the live test. An organization authorization was visible
through `slack auth list`, but no live app installation, credential handoff,
Socket Mode connection, or Slack reply was claimed as verified.

When the operator supplies the workspace and test channel:

1. Run `tag add`, select the authorized organization, and enter that workspace's
   `T…` ID. Confirm the saved settings retain its separate `E…` organization ID.
2. Create a test app, approve its single-workspace installation, and resume setup
   after any organization-admin approval. Record the App ID across the pause.
3. Complete credential validation and selected-channel memory setup. Verify the
   Socket Mode heartbeat and a task's actual Slack reply; record the message URL.
4. Exercise an authorized search and confirm retrieved sources belong to the
   selected Tag/workspace. Check that another workspace cannot invoke this Tag.
5. Restart and verify migration idempotence and the same app/workspace identity.

Do not close #148 or mark live verification complete until this evidence exists.
Any later permanent Slack app deletion requires the separate exact-target
confirmation prescribed by AGENTS.md.

## Provider references

- [Slack CLI on Enterprise Grid](https://docs.slack.dev/tools/slack-cli/guides/using-slack-cli-on-an-enterprise-grid-organization/)
- [Organization-ready apps and workspace grants](https://docs.slack.dev/enterprise/organization-ready-apps/)
- [Checking an organization's app workspace grants](https://docs.slack.dev/reference/methods/auth.teams.list/)
- [Slack CLI grant selection implementation](https://github.com/slackapi/slack-cli/blob/main/internal/prompts/app_select.go)
