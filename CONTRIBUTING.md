# Contributing

Tag is an early alpha. Small, reviewable changes with tests and clear security
impact are welcome.

1. Create a branch from `main`.
2. Keep credentials, `.env`, logs, and `.runtime` state out of commits.
3. Run `./scripts/ci_check.sh`.
4. Open a pull request explaining the user-visible behavior, test evidence, and
   any change to chat, agent, credential, or MFS scope boundaries.

Use public GitHub issues for ordinary bugs and features. Report suspected
vulnerabilities through the private path in [SECURITY.md](SECURITY.md).

By contributing, you agree that your contribution is licensed under the
repository's Apache License 2.0.
