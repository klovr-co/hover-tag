# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`.
- **Apply or remove labels**: `gh issue edit <number> --add-label "..."` or `--remove-label "..."`.
- **Close**: `gh issue close <number> --comment "..."`.

Infer the repo from `git remote -v`; `gh` does this automatically inside the clone.

## Project board

The existing organization project [#3, Tag Features](https://github.com/orgs/klovr-co/projects/3)
is a feature catalog, not a flat task backlog. Read its README, fields, and
representative items before adding or changing entries. Do not create a
replacement project or automatically add every implementation task.

- Use short capability titles, such as "Guided setup and management", without
  task prefixes. Keep implementation details in the body or linked issue.
- Future ideas start as draft items; convert them to GitHub issues when concrete
  work begins. Honor an explicit request for a repository issue and preserve
  existing issue history rather than replacing it solely for formatting.
- Populate Status, Tier, Area, Verification, and Version using existing values.
  New ideas normally use Status `Idea`, Verification `Missing`, and Version
  `TBD`; leave Maturity unset until there is an implementation to assess.
- Status `Shipped` means implemented. Maturity records product confidence;
  Verification records the strongest completed check for the whole feature.
  Do not infer feature completion from a partial patch's passing tests.
- Version is the first tagged release containing the feature. Use `Unreleased`
  only for implemented features after the latest tag, not planned work.
- Choose Core/Optional and the ownership Area based on the feature. Reuse the
  existing schema; do not introduce new fields or options without a need.

For a feature tracked by an existing issue, add it with:

```bash
gh project item-add 3 --owner klovr-co --url https://github.com/klovr-co/hover-tag/issues/NUMBER
```

Replace `NUMBER` with the issue number, then set its catalog fields with
`gh project item-edit`. Read the item back to verify both membership and fields
before reporting completion.

### Authentication fallback

If a Projects command fails with `Resource not accessible by integration`, run
`gh auth status`. An environment-provided `GH_TOKEN` or `GITHUB_TOKEN` can override
a saved CLI login that already has the `project` scope.

When the saved login is the intended user account and has project access, retry
the authorized operation using that login for this command only:

```bash
env -u GH_TOKEN -u GITHUB_TOKEN gh project item-add 3 --owner klovr-co --url https://github.com/klovr-co/hover-tag/issues/NUMBER
```

Do not print token values or change global authentication settings. If no suitable
saved login exists or access is still denied, report the blocker and retain the
issue URL. Do not claim board placement succeeded. GitHub App users can approve
permissions requested by the app; they cannot independently add arbitrary app
permissions.

## Pull requests as a triage surface

**PRs as a request surface: no.**

GitHub shares one number space across issues and PRs. Resolve an ambiguous
reference with `gh pr view <number>` and fall back to `gh issue view <number>`.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.

## Wayfinding operations

- Use a GitHub issue labelled `wayfinder:map` for a wayfinding map.
- Link child tickets as GitHub sub-issues when supported; otherwise use a task
  list in the map and add `Part of #<map>` to each child.
- Represent blockers with GitHub's native issue dependencies when supported.
  If unavailable, add `Blocked by: #<number>` to the ticket body.
- Claim work with `gh issue edit <number> --add-assignee @me`.
- Resolve work by commenting with evidence, closing the issue, and updating the
  parent map when one exists.
