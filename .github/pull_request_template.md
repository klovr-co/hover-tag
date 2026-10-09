## Summary

<!-- What changed and why. -->

## Release target

Choose one before merging. See "Release lines" in `RELEASE.md`.

- [ ] **Current line** (`VERSION` on `main`): merge normally.
- [ ] **Also fix the older stable release:** merge to `main`, then cherry-pick to `release/vX.Y.x`.
- [ ] **A later version:** keep this PR as a draft with a `target:vX.Y` label, and merge it after the current line ships stable.
