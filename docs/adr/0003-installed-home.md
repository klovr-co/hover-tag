# Separate TAG's installed home from its source checkout

TAG owns a persistent, platform-native application home with an optional absolute
`TAG_HOME` override. Application releases are replaceable; configuration, the
agent workspace, integrations, state, and temporary files live outside releases.
This lets upgrades and development worktrees coexist without moving personal
skills or credentials into source control.

Shared lifecycle logic lives in Python, with shell and PowerShell entry points.
An atomic `current.json` selects a release rather than a symlink, so native
Windows does not require symlink privileges. Background startup is explicit;
automatic startup after login is deferred.

Backend global authentication, skills, and MCP settings remain inherited.
Codex runs in the persistent workspace; TAG layers that workspace's MCP server
definitions through command-line configuration because per-invocation project
trust did not enable project configuration in the tested Codex CLI 0.147.0.
Only MCP definitions are layered, and TAG never edits global backend settings.
