---
name: tag-release
description: Prepare, qualify, promote, or inspect Tag alpha, beta, and stable releases. Use for Tag release candidates, release evidence, channel promotion, GitHub release drafts, publication, or changing the default installer channel; not for ordinary feature PRs.
---

# Tag Release

Operate Tag releases through the repository's checked-in contract and automation. Keep `RELEASE.md` canonical; if this skill conflicts with it, follow `RELEASE.md` and report the mismatch.

## Establish the requested stage

Determine whether the user wants to inspect release state, prepare a candidate, record qualification evidence, create a draft, publish a draft, or change the default installation channel. Do not turn a read-only status question into a release mutation. Do not combine later stages merely because the user authorized an earlier one.

Before changing anything, read `CONTEXT.md`, `RELEASE.md`, `VERSION`, `release-channels.json`, `.github/workflows/prepare-release.yml`, and the relevant files under `docs/releases/` and `docs/release-evidence/`. Inspect the worktree and preserve unrelated changes. Fetch tags and `origin/main` before calculating a version or validating a commit when network access is available.

Use the published GitHub releases, not `VERSION` alone, to identify the latest immutable release. Treat `edge` as a moving build, never as a SemVer predecessor.

## Preserve the release model

- An alpha, beta, or stable release is a new immutable release. Never rename, replace, or delete an earlier release as part of promotion.
- Ordinary merges on an alpha or beta source line publish the next numbered prerelease automatically. There is no `release:beta` or `release:stable` PR label.
- Moving from alpha to beta requires a focused source change to `VERSION`; its merge publishes `beta.1` automatically after CI and clean-install checks pass. Later eligible merges publish the next numbered beta.
- Stable is an explicit owner action through **Prepare stable release**.
- The source version and the release notes must describe the intended stable version before the candidate is tested.
- Only the matching evidence file may change after live candidate testing. Any other change requires a new candidate and another live qualification.
- Reuse the retained artifact for the exact promoted commit. Never rebuild locally and attach substitute assets.

## Inspect release state

Report at least the source `VERSION`, configured default channel, latest published SemVer release, whether a matching release already exists, relevant workflow results, and whether the required retained artifact is still available. Distinguish local state, merged `main` state, a GitHub draft, and a published release.

Useful read-only commands include:

```sh
git status --short
git fetch origin main --tags
gh release list --repo klovr-co/hover-tag --limit 30
gh run list --repo klovr-co/hover-tag --limit 30
```

Do not expose tokens or dump unbounded logs.

## Prepare beta

Derive the next beta version from the latest published release and confirm it
with the repository validator. Prepare a focused change with `VERSION` set to
the complete semantic beta version `<major>.<minor>.<patch>-beta.1` (for example,
`0.2.0-beta.1`) and factual release notes at `docs/releases/v<VERSION>.md`. Keep
the default channel set to `stable`. Do not create release evidence or dispatch
a manual workflow. After the change merges, CI, clean-install checks, and the
edge workflow publish the immutable beta from the retained artifact
automatically. Verify the published tag, target commit, prerelease setting, and
assets.

## Prepare stable

Derive the next version from the latest published release and confirm it with the repository validator. For the same version core, the expected progression is numbered alpha to `beta.1`, later numbered betas if supported by the contract, then stable with no prerelease suffix. Do not invent a transition when published history is ambiguous.

Prepare a focused candidate change containing:

- `VERSION` set to the intended version.
- `docs/releases/v<VERSION>.md` with factual release notes and accurate limitations.

Do not claim qualification before it has happened. Keep the default channel set to `stable`; preparing a prerelease must not change it. Run the relevant tests, `python3 scripts/release_check.py`, and:

```sh
python3 scripts/release_automation.py validate-candidate \
  --version "$(sed -n '1p' VERSION)" --phase stable
```

Prepare a commit or PR when requested. Do not merge it unless the user requested the merge and repository checks permit it.

## Record qualification evidence

The candidate must be a full 40-character commit SHA on `main`. Confirm that the live Slack task and all recorded checks exercised that candidate, not a later working tree or merely an equivalent diff.

Create `docs/release-evidence/v<VERSION>.md` only from observed results. Include the exact candidate SHA and the gate lines required by `scripts/release_preflight.sh`. Mark missing or unverified gates `PENDING` or `FAIL`; never infer `PASS`. Link the exact GitHub CI and clean-install runs. Record enough detail to identify the live Slack request and reply without including credentials or sensitive message content.

After qualification, ensure the evidence file is the only path changed from the candidate commit. Run:

```sh
./scripts/release_preflight.sh
```

If any non-evidence path changed, stop and require a new candidate and live qualification.

## Create the stable GitHub draft

Use the exact promoted `main` commit, not the earlier live candidate SHA when an evidence-only commit followed it. Verify CI, clean-install checks, and retained artifact availability for that promoted SHA. Then dispatch:

```sh
gh workflow run prepare-release.yml \
  --repo klovr-co/hover-tag \
  -f commit_sha=<full-promoted-main-sha>
```

Wait for the workflow when the user asked for draft creation, inspect its result, and return the draft URL. Confirm the stable draft is not a prerelease. A successful workflow creates a draft; it does not authorize publication.

## Publish only with explicit approval

Immediately before publication, show the exact version, tag, phase, target commit, draft URL, and prerelease setting. Ask for explicit confirmation of that target unless the user has just provided equally specific publication approval. Do not let an agent answer the confirmation on the user's behalf.

Before publishing, ensure the evidence contains `Release publication approval: **PASS**` based on real owner approval and run:

```sh
./scripts/release_preflight.sh --publish
```

Publish only the inspected draft. Verify the published tag, target commit, prerelease setting, and assets afterward. Never delete or overwrite an existing immutable release to recover from an error; stop and report the mismatch.

## Preserve the stable default installation channel

`release-channels.json` controls bare installs; it does not publish a release. Its `default_channel` is always `stable`, including before the first stable release exists. Never switch the default to alpha, beta, or edge as a release convenience, and never make a bare install silently fall back to a prerelease. Before the first stable publication, report clearly that the bare installer has no eligible release.

Keep explicit alpha, beta, edge, or pinned-version installation paths available. Alpha and beta should use separate friendly Hover URLs; treat their routing as website work unless its implementation is present in this checkout. After a stable release is published, validate that `https://hover.team/tag/install` resolves it.

## Hand off clearly

State what exists now, what was verified, the exact commit and version involved, and the next human or automated gate. Never describe a prepared candidate as published, a draft as public, or a healthy service as proof of a live Slack qualification.
