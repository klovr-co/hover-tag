# Contributing

Tag is an early alpha. Small, reviewable changes with tests and clear security
impact are welcome.

1. Create a branch from `main`.
2. Keep credentials, `.env`, logs, and `.runtime` state out of commits.
3. Run `./scripts/ci_check.sh`.
4. Open a pull request explaining the user-visible behavior, test evidence, and
   any change to chat, agent, credential, or MFS scope boundaries.

## Choosing a release

`main` is the current release line, which `VERSION` names (for example,
`0.4.0-alpha`). Every merge to `main` can publish a prerelease, so before you
merge, choose one of these targets in the PR template's **Release target**
checklist.

### Current line (most PRs)

Branch from `main`, open the PR, and add the `queue` label when it is ready.
Mergify merges it (see "Merging" below).

```bash
git switch -c my-change origin/main
gh pr create --base main
```

### A fix that older stable users also need

Merge the fix to `main` first. If users of the older stable release need it
before the next release, add the `backport:vX.Y.x` label (for example,
`backport:v0.3.x`) to the merged PR, or run the **Backport** workflow. It opens
a pull request that cherry-picks the commit onto `release/vX.Y.x`, creating the
branch from the line's newest stable tag if needed. Review and merge that pull
request. If the cherry-pick conflicts, the workflow comments on the original PR,
and a maintainer copies the commit by hand:

```bash
git switch -c backport/pr-123-to-v0.3.x origin/release/v0.3.x
git cherry-pick -x <commit-on-main>
```

- Never merge `main` into a `release/vX.Y.x` branch, because that pulls in
  unreleased work.
- Never land a fix only on the maintenance branch. It must be on `main` too.

Merging into `release/vX.Y.x` publishes nothing. CI, the install smoke test,
and a kept build run, but stable users get no update. Fixes can collect on the
branch until a maintainer releases them as a stable patch, such as `v0.3.1`:

1. Merge a pull request into `release/vX.Y.x` that sets `VERSION` to the patch
   (for example, `0.3.1`) and adds its `CHANGELOG.md` entry and
   `docs/releases/v0.3.1.md`.
2. Run **Prepare stable release** with `branch` set to `release/vX.Y.x` and the
   tested commit's SHA. It creates a draft release.
3. Publish the draft. Stable users get the update only after this step.

See "Patching an older stable release" in [RELEASE.md](RELEASE.md).

### A later version

For example, a feature planned for 0.5 while `main` is 0.4:

1. Open the PR as a **draft** with a `target:v0.5` label.

   ```bash
   gh pr create --base main --draft --label target:v0.5
   ```

2. Don't merge it, even when it is approved.
3. Merge `main` into it every week or so, to keep conflicts small:

   ```bash
   git fetch origin
   git merge origin/main
   git push
   ```

4. After the current line ships stable, a maintainer sets `VERSION` to the next
   line (for example, `0.5.0-alpha`). Remove the `target:v0.5` label, mark the
   PR ready for review, and queue it.

We don't use feature flags to hide work for later versions.

### Release labels

You usually don't need one. See "Pull request release behavior" in
[AGENTS.md](AGENTS.md) and [RELEASE.md](RELEASE.md) for `release:skip`,
`release:next-patch`, and `release:next-minor`. Add a release label before you
queue the PR.

## Merging

[Mergify](https://mergify.com) merges pull requests into `main` and
`release/vX.Y.x`. Don't press GitHub's merge button. When a PR is ready, add the
`queue` label or comment `@mergifyio queue`. Mergify then:

1. Waits until every CI and clean-install check passes, CodeRabbit has
   approved, every review thread is resolved, and no review requests changes.
   Fix each CodeRabbit comment, or reply with why not, and resolve its thread;
   CodeRabbit approves once its threads are resolved. If it skipped the review
   for its rate limit, comment `@coderabbitai review`.
2. Merges the base branch into the PR and waits for the checks again.
3. Squash-merges the PR, one PR at a time, so prerelease numbers follow merge
   order.

Mergify doesn't queue a draft, a PR with a `target:vX.Y` label, or a PR with
more than one `release:*` label. To take a PR out of the queue, remove the
`queue` label or comment `@mergifyio dequeue`. The rules are in
[`.mergify.yml`](.mergify.yml); keep its check list in step with the job names
in `.github/workflows/ci.yml` and `install-smoke.yml`.

Use public GitHub issues for ordinary bugs and features. Report suspected
vulnerabilities through the private path in [SECURITY.md](SECURITY.md).

By contributing, you agree that your contribution is licensed under the
repository's Apache License 2.0.
