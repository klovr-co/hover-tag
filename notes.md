# Open Tag Launch Notes

## Launch decision

Yes, we can launch Open Tag as an open-source alpha/reference implementation if
we are transparent about its current limitations and comply fully with the
Apache License 2.0 requirements inherited from the upstream MFS Open Tag
example.

The launch messaging must clearly state that Open Tag is not yet a production
security boundary. It should initially be used in an isolated Slack channel and
workspace on a non-production machine.

## Release hygiene plan

- Confirm the provenance of all upstream-derived files and modifications.
- Add the appropriate Apache 2.0 `LICENSE` file.
- Add a `NOTICE` file if required by the upstream distribution and preserve all
  required copyright and attribution notices.
- Keep the README's Origins and Attribution section accurate and prominent.
- Add a version and initial release tag.
- Add a changelog or release notes covering the supported transports, backends,
  known limitations, and security model.
- Pin runtime dependencies so a clean installation is reproducible.
- Add CI that runs the complete test suite with the Slack runtime dependency.
- Verify that `.env`, credentials, runtime files, and logs cannot be committed or
  packaged into a release.
- Test the documented installation command from a clean machine or isolated
  environment.
- Publish a support statement describing the tested Python, macOS/Linux, Codex,
  Claude, Slack, Zulip, and MFS versions.

## End-to-end smoke-test plan

Do not consider the launch build ready until these tests have been performed and
their results recorded:

1. Start from a clean checkout with no existing Open Tag configuration.
2. Follow only the public installation documentation.
3. Configure and start MFS, then confirm its health endpoint and an indexed
   source.
4. Run the Open Tag doctor and require every applicable check to pass.
5. Mention the bot in the configured Slack sandbox channel and confirm the full
   mention → thread context → MFS retrieval → backend execution → Slack reply
   path.
6. Ask for a full-channel summary and verify that the correct indexed Slack
   channel is discovered and read.
7. Test a follow-up request in the same thread.
8. Test a text attachment, an image attachment, Markdown rendering, and a reply
   long enough to require multiple Slack messages.
9. Mention the bot from a non-allowed Slack channel and verify that no backend
   process runs.
10. Attempt an MFS sibling-prefix path, `../` traversal, and percent-encoded
    traversal, and verify that each is rejected.
11. Trigger a backend failure and timeout and verify that the user receives a
    useful error without credentials or sensitive output being exposed.
12. Repeat the applicable flow for Zulip native and the pinned ZulipMCP engine,
    or explicitly mark either engine experimental and outside the initial launch
    support promise.
13. Stop and restart Open Tag, then confirm it reconnects and can complete
    another request.

Record the environment, commands, expected results, actual results, screenshots,
and any remediation for every failed step.

## Installation and deployment plan

The current setup is too complicated. The primary installation path should be a
single guided action, with the detailed manual guide retained for troubleshooting.

### Target experience

A new user should be able to go from the README to a working sandbox bot without
already understanding MFS, Slack Socket Mode, environment variables, or the
backend command line.

Preferred entry point:

```text
Install Open Tag → run one setup command → follow provider authorization links →
pass automatic preflight → start the bot → send a test mention
```

### Options to evaluate

1. **One-command local installer:** install dependencies, create private
   configuration, guide Slack/Zulip authorization, configure an initial MFS
   source, run preflight, and start the bot.
2. **Desktop one-click setup:** turn the existing setup and control commands into
   a coherent wizard with progress, actionable errors, and a final test button.
3. **Container deployment:** provide a pinned Docker image and Compose template
   for MFS and Open Tag, while mounting backend credentials and workspaces
   explicitly.
4. **Hosted deployment template:** consider this only after defining how Codex or
   Claude authentication, persistent storage, secrets, sandboxing, and workspace
   access work safely on the target host.

### Documentation requirements

- Put a five-minute quick start at the top of the README.
- Separate Slack-only, Zulip-only, and advanced dual-transport paths.
- State exactly which administrator actions cannot be automated.
- Include copyable Slack app configuration or an app manifest where possible.
- Explain each required credential without ever asking users to paste it into an
  agent conversation.
- Add a troubleshooting table mapping doctor failures to concrete fixes.
- Include a clean uninstall and credential-revocation procedure.
- Provide a short demo video or annotated screenshots matching the current UI.

## Launch exit criteria

- Apache 2.0 licensing and attribution are complete.
- A clean installation succeeds using the public guide.
- The end-to-end smoke-test matrix passes for every advertised transport and
  backend combination.
- The easy installation path is validated by someone who did not build Open Tag.
- Release artifacts contain no secrets, logs, local paths, or generated runtime
  state.
- Launch messaging accurately describes Open Tag as an alpha/reference
  implementation and communicates its security limitations.

## v0.1.0-alpha progress — 2026-09-09

- Apache-2.0 license, NOTICE, modification notices, security policy, and release
  contract: complete.
- Guided installer, portable `tag` command, pinned dependencies, and Slack app
  manifest: complete.
- Local CI gate: pass (35 tests).
- GitHub CI: pass on macOS/Linux and Python 3.10/3.14.
- Clean-install smoke: pass on macOS and Linux.
- Live Slack preflight and Socket Mode connection: pass.
- Human-authored Slack mention and exactly one correlated OpenMax reply: pass;
  bot self-mentions are correctly suppressed by Slack.
- Public repository visibility, final merge/tag/prerelease, and X post: pending
  explicit owner approval after the live mention passes.
