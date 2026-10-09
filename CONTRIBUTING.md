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

Branch from `main`, open the PR, and merge it when it is approved and CI
passes.

```bash
git switch -c my-change origin/main
gh pr create --base main
```

### A fix that older stable users also need

Merge the fix to `main` first. If users of the older stable release need it
before the next release, a maintainer copies it onto that release's
maintenance branch:

```bash
# Only the first time: create the branch from the stable tag
git switch -c release/v0.3.x v0.3.0
git push -u origin release/v0.3.x

# For each fix: copy the merged commit from main
git switch release/v0.3.x
git cherry-pick -x <commit-on-main>
git push
```

- Never merge `main` into a `release/vX.Y.x` branch, because that pulls in
  unreleased work.
- Never land a fix only on the maintenance branch. It must be on `main` too.
- The release automation publishes only from `main`, so ask a maintainer
  before you rely on a patch release such as `v0.3.1`.

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
   line (for example, `0.5.0-alpha`). Mark the PR ready for review and merge
   it.

We don't use feature flags to hide work for later versions.

### Release labels

You usually don't need one. See "Pull request release behavior" in
[AGENTS.md](AGENTS.md) and [RELEASE.md](RELEASE.md) for `release:skip`,
`release:next-patch`, and `release:next-minor`.

Use public GitHub issues for ordinary bugs and features. Report suspected
vulnerabilities through the private path in [SECURITY.md](SECURITY.md).

By contributing, you agree that your contribution is licensed under the
repository's Apache License 2.0.
