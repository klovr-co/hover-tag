# Developer sandboxes and Enterprise organizations

In `tag add`, select the account marked **organization**, then enter the intended
workspace's `T…` Team ID. Open that workspace in a browser and copy the ID from
`app.slack.com/client/T…`. The `E…` organization ID is kept separately; it is not
a workspace ID. You do not need to repeat Slack authorization.

Tag creates or links the app through your organization authorization and requests
access to only the selected workspace. If Slack requires administrator approval,
leave setup and resume it after approval. The saved App ID prevents a retry from
creating another app. Tag preserves an existing app's grants and verifies that
they include the selected workspace. If access is missing, an organization admin
must add the app to that workspace before you retry.

Each Tag still responds in one workspace and retrieves only its approved channel
memory. The managed memory server supports organization tokens with an explicit
workspace; an externally managed MFS server needs equivalent workspace-aware
Slack connector support. Existing workspace authorizations continue to work.

