## Summary

<!-- What changed and why. -->

## Release target

Choose one before merging. See "Release lines" in `RELEASE.md`.

- [ ] **Current line** (`VERSION` on `main`): merge normally.
- [ ] **Also fix the older stable release:** merge to `main`, then cherry-pick to `release/vX.Y.x`.
- [ ] **A later version:** label it `target:vX.Y` and merge it behind a feature flag that is off by default. If a flag is impractical, keep it as a draft until the current line ships stable.
