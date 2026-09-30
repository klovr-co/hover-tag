from __future__ import annotations

import unittest

from scripts.tag_activity_details import command_identity, item_activity_details
from scripts.tag_activity_labels import readable_activity_title


class ReadableActivityTitleTests(unittest.TestCase):
    def test_commands_become_readable_without_inventing_purpose(self) -> None:
        cases = {
            "cat runtime-agent.md": "Reading runtime-agent.md",
            "head launch plan.md": "Reading start of launch plan.md",
            "tail server.log": "Reading end of server.log",
            "ls": "Listing files",
            "find": "Finding files",
            "rg": "Searching text",
            "sed": "Processing text",
            "python report.py": "Running report.py",
            "python3.12 (inline)": "Running Python code",
            "python -m pytest": "Running tests",
            "python -m http.server": "Running http.server",
            "node build.js": "Running build.js",
            "pytest": "Running tests",
            "custom-tool": "Running custom-tool",
            "gmail/send_email": "Sending Gmail email",
            "File change · app.py, test_app.py": "Updating app.py, test_app.py",
            "Web search": "Searching the web",
            "Image view": "Viewing an image",
            "Command": "Running a command",
            "": "Running a command",
        }
        for tool, expected in cases.items():
            with self.subTest(tool=tool):
                self.assertEqual(readable_activity_title(tool, "Running a command…"), expected)

    def test_formatted_command_never_uses_private_arguments_or_results(self) -> None:
        details = item_activity_details({
            "type": "commandExecution",
            "command": "/bin/zsh -lc 'cd /private/work && python scripts/report.py --token private-token'",
            "aggregatedOutput": "private result",
        }, completed=True)
        title = readable_activity_title(details["tool"], "Running a command…")
        self.assertEqual(title, "Running report.py")

    def test_shell_commands_render_end_to_end(self) -> None:
        cases = {
            "cd /private/work && git -c credential.helper=private-value diff --name-only": "Reviewing changes",
            "env TOKEN=private-value /bin/bash -lc 'git status --short'": "Checking repository status",
            "sudo -n -u root uv run python -m pytest /private/tests": "Running tests",
            "git diff && npm test": "Reviewing changes; Running tests",
            "git diff || git status": "Reviewing changes or Checking repository status",
            "cat '/private/my file.py' | grep private-pattern": "Reading my file.py → Searching text",
            "cd /private/work\ngit status\npnpm run build": "Checking repository status; Building the project",
            "python - <<'PY'\nprint('private body')\nPY": "Running Python code",
            "python -c 'print(\"private; body\")'": "Running Python code",
            "printf 'private; text' && git status": "Running printf; Checking repository status",
            "cat 'literal && name.txt'": "Reading files",
            "echo $(private-command)": "Running a command",
            "if true; then git push; fi": "Running a command",
            "exit 0; git push": "Running a command",
            "cat input.txt > /private/output.txt": "Running a command",
            "npm install --token private-value": "Installing dependencies",
            "pnpm run lint": "Checking code",
            "yarn typecheck": "Checking types",
            "cargo test": "Running tests",
            "go build": "Building the project",
            "ruff format src": "Formatting code",
            "npx --yes vitest run": "Running tests",
            "pnpm exec tsc --noEmit": "Checking types",
            "cp /private/source /private/destination": "Copying to destination",
            "mv /private/source /private/destination": "Moving to destination",
            "cp -t /private/destination /private/first /private/second": "Copying to destination",
            "mkdir -p /private/folder": "Creating folder folder",
            "rm -rf /private/old-folder": "Deleting old-folder",
            "git log --oneline": "Reviewing commit history",
            "git push origin private-branch": "Pushing changes",
            "pytest --version": "Checking pytest version",
            "rg --files": "Listing files",
            "custom-tool private-value": "Running custom-tool",
            "# private comment\ngit status": "Checking repository status",
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                title = readable_activity_title(command_identity(command), "Running a command…")
                self.assertEqual(title, expected)

    def test_connected_tools_and_file_actions(self) -> None:
        for tool, expected in {
            "codex_apps/google_drive_search": "Searching Google Drive",
            "slack/search_messages": "Searching Slack messages",
            "github/list_pull_requests": "Listing GitHub pull requests",
            "unknown/custom_call": "Using unknown/custom_call",
        }.items():
            self.assertEqual(readable_activity_title(tool, "Using a connected tool…"), expected)
        for kind, expected in (("add", "Creating app.py"), ("delete", "Deleting app.py"), ("update", "Updating app.py")):
            details = item_activity_details({"type": "fileChange", "changes": [
                {"path": "/private/app.py", "kind": {"type": kind}, "diff": "private content"},
            ]}, completed=False)
            self.assertEqual(readable_activity_title(details["tool"], "Updating files…"), expected)
        details = item_activity_details({"type": "fileChange", "changes": [
            {"path": "/private/old.py", "kind": {"type": "update", "movePath": "/private/new.py"}},
        ]}, completed=False)
        self.assertEqual(readable_activity_title(details["tool"], "Updating files…"), "Moving old.py → new.py")

    def test_malformed_or_large_commands_have_bounded_fallbacks(self) -> None:
        for command in (None, {}, "", "cat 'unfinished", "x" * 9000, "git status &", "env -S private-value", "sudo -bad private-value"):
            with self.subTest(command=command):
                self.assertEqual(command_identity(command), "Command")
        identity = command_identity(" && ".join(["git status"] * 12))
        self.assertLessEqual(len(identity), 160)
        self.assertIn("more commands", identity)

    def test_lifecycle_does_not_claim_success_for_failure_or_missing_events(self) -> None:
        for status, expected in {
            "running": "Reading app.py", "completed": "Read app.py",
            "failed": "Failed · Reading app.py", "declined": "Declined · Reading app.py",
            "interrupted": "Stopped · Reading app.py", "unknown": "Incomplete · Reading app.py",
        }.items():
            self.assertEqual(readable_activity_title("cat app.py", "Reading files…", status), expected)
        self.assertEqual(readable_activity_title("git diff && pytest", "Running a command…", "completed"),
                         "Finished · Reviewing changes; Running tests")


if __name__ == "__main__":
    unittest.main()
