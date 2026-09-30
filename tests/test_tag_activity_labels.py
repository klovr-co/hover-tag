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
            "ruff format src": "Formatting code · src",
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

class GranularActivityTests(unittest.TestCase):
    def test_http_requests_name_the_target_and_download(self) -> None:
        cases = {
            'curl -fsSL https://example.com/assets/logo.png -o /tmp/logo.png':
                'Downloading example.com/logo.png → logo.png',
            'curl -sSI https://example.com/docs': 'Checking headers for example.com/docs',
            'curl -I https://example.com/docs': 'Checking headers for example.com/docs',
            'curl -X POST -H "Authorization: Bearer private-token" -d "private body" https://api.example.com/v1/items':
                'Sending POST to api.example.com/items',
            'curl --url=https://user:private-password@example.com/private/report.csv?token=private-token --output=/tmp/report.csv':
                'Downloading example.com/report.csv → report.csv',
            'curl -s https://one.example/a.json && curl -s https://two.example/b.json':
                'Fetching one.example/a.json; Fetching two.example/b.json',
            'curl -sS https://example.com/data.json | python -m json.tool':
                'Fetching example.com/data.json → Running json.tool',
            'curl -H "https://private.example/secret" https://example.com':
                'Fetching example.com',
            'curl --config private-config': 'Running curl',
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                details = item_activity_details({'type': 'commandExecution', 'command': command}, completed=False)
                title = readable_activity_title(details['tool'], 'Running a command…')
                self.assertEqual(expected, title)
                self.assertNotIn('private', title)

    def test_python_names_scripts_modules_and_literal_file_operands(self) -> None:
        cases = {
            'python3 -uB -X dev -W ignore /private/scripts/report.py --token private-token': 'Running report.py',
            'uv run python3 -Xutf8 /private/render.py': 'Running render.py',
            'python -m json.tool /private/data.json': 'Running json.tool',
            "python -c 'from pathlib import Path; print(Path(\"/private/report.csv\").read_text())'":
                'Running Python · report.csv',
            "python - <<'PY'\nwith open('/private/data.json') as f:\n    print(f.read())\nPY":
                'Running Python · data.json',
            "python -c 'print(\"private-file.py\")'": 'Running Python code',
            "python -c 'from PIL import Image; filename = \"/private/logo.png\"; Image.open(filename)'":
                'Running Python · logo.png',
            "python -c 'open(variable)'": 'Running Python code',
            "python -c 'invalid python'": 'Running Python code',
            "python -c 'open(\"/private/a.json\"); open(\"/private/b.json\")'":
                'Running Python · a.json, b.json',
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(expected, readable_activity_title(command_identity(command), 'Running a command…'))

    def test_granular_completion_keeps_the_target(self) -> None:
        tool = command_identity('curl -s https://example.com/data.json')
        self.assertEqual('Fetched example.com/data.json', readable_activity_title(tool, '', 'completed'))
        self.assertEqual('Failed · Fetching example.com/data.json', readable_activity_title(tool, '', 'failed'))

    def test_image_view_names_the_image(self) -> None:
        details = item_activity_details({'type': 'imageView', 'path': '/private/images/logo.png'}, completed=False)
        self.assertEqual('Viewing logo.png', readable_activity_title(details['tool'], 'Viewing an image…'))


class OtherToolTargetTests(unittest.TestCase):
    def test_known_tools_preserve_targets_without_patterns_or_option_values(self) -> None:
        cases = {
            'rg -n "private-pattern" /private/src': 'Searching text · src',
            'rg --files /private/src': 'Listing files · src',
            'grep -e private-pattern /private/server.log': 'Searching text · server.log',
            "sed -n '1,40p' /private/config.py": 'Processing text · config.py',
            "jq '.private_field' /private/data.json": 'Processing JSON · data.json',
            'find /private/assets -name private-pattern': 'Finding files · assets',
            'ls -la /private/assets': 'Listing files · assets',
            'du -sh /private/build': 'Checking disk usage · build',
            'wc -l /private/results.csv': 'Counting content · results.csv',
            'file /private/image.png': 'Checking file type · image.png',
            'git diff -- /private/app.py': 'Reviewing changes · app.py',
            'git add /private/app.py': 'Staging changes · app.py',
            'pytest -k private-pattern tests/test_api.py': 'Running tests · test_api.py',
            'npx vitest run tests/api.test.ts': 'Running tests · api.test.ts',
            'eslint --fix src/app.ts': 'Checking code · app.ts',
            'npm run docs:sync -- --token private-token': 'Running script docs:sync',
            'ruff check src/app.py': 'Checking code · app.py',
            'make preview': 'Running make · preview',
            'make TOKEN=private-token': 'Running make',
            'bash scripts/deploy.sh': 'Running bash · deploy.sh',
            'tsx scripts/report.ts': 'Running tsx · report.ts',
            'node --require private-hook scripts/report.js': 'Running report.js',
            'node --test tests/api.test.js': 'Running tests · api.test.js',
            'ruby scripts/report.rb': 'Running report.rb',
            'perl scripts/report.pl': 'Running report.pl',
            'wget -q -O /private/logo.png https://example.com/images/logo.png': 'Downloading example.com/logo.png → logo.png',
            'rg --unknown private-value file.txt': 'Searching text',
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                title = readable_activity_title(command_identity(command), 'Running a command…')
                self.assertEqual(expected, title)
                self.assertNotIn('private', title)


class SkillActivityTests(unittest.TestCase):
    def test_skill_reads_keep_the_skill_name_not_the_full_path(self) -> None:
        cases = {
            'cat /private/skills/browser-use/SKILL.md': 'Reading browser-use skill',
            'head -n 40 /private/skills/imagegen/SKILL.md': 'Reading start of imagegen skill',
            "sed -n '1,80p' /private/skills/tag-release/SKILL.md": 'Processing text · tag-release skill',
            'cat skills/browser-use/SKILL.md skills/imagegen/SKILL.md': 'Reading browser-use skill, imagegen skill',
            'cat SKILL.md': 'Reading SKILL.md',
            'cat ../SKILL.md': 'Reading SKILL.md',
            'cat /private/skills/browser-use/README.md': 'Reading README.md',
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                title = readable_activity_title(command_identity(command), 'Reading files…')
                self.assertEqual(expected, title)
                self.assertNotIn('/private', title)
        one = command_identity('cat /private/skills/browser-use/SKILL.md')
        two = command_identity('cat /private/skills/imagegen/SKILL.md')
        self.assertNotEqual(one, two)
        self.assertEqual('Read browser-use skill', readable_activity_title(one, '', 'completed'))
