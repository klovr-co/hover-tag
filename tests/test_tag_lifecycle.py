from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


class TagLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        repository = Path(__file__).resolve().parents[1]
        shutil.copy2(repository / "tag", self.root / "tag")
        shutil.copy2(
            repository / "requirements-runtime.txt",
            self.root / "requirements-runtime.txt",
        )
        (self.root / "scripts").mkdir()
        (self.root / "scripts" / "opentag_doctor.py").write_text(
            "raise SystemExit(0)\n", encoding="utf-8"
        )
        (self.root / ".env").write_text(
            "export OPENTAG_BACKEND='codex'\n", encoding="utf-8"
        )
        self.fake_bin = self.root / "fake-bin"
        self.fake_bin.mkdir()
        self.write_executable(
            "curl",
            "#!/bin/sh\n[ \"${FAKE_CURL_HEALTHY:-0}\" = 1 ]\n",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_executable(self, name: str, content: str) -> None:
        path = self.fake_bin / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def environment(self, **overrides: str) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{self.fake_bin}{os.pathsep}{environment['PATH']}",
                "OPENTAG_ENV_FILE": str(self.root / ".env"),
            }
        )
        environment.update(overrides)
        return environment

    def run_tag(self, command: str, **environment: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.root / "tag"), command],
            cwd=self.root,
            env=self.environment(**environment),
            text=True,
            capture_output=True,
            check=False,
        )

    def test_status_fails_when_required_services_are_unhealthy(self) -> None:
        completed = self.run_tag("status")

        self.assertNotEqual(0, completed.returncode)
        self.assertIn("MFS: stopped or unhealthy", completed.stdout)
        self.assertIn("Slack bridge: stopped", completed.stdout)

    def test_start_fails_and_prints_log_when_bridge_exits(self) -> None:
        self.write_executable(
            "uv",
            "#!/bin/sh\nprintf 'bridge exploded\\n'\nexit 7\n",
        )

        completed = self.run_tag(
            "start", FAKE_CURL_HEALTHY="1", OPENTAG_STARTUP_ATTEMPTS="2"
        )

        self.assertNotEqual(0, completed.returncode)
        self.assertIn("opentag-slack-bridge.log", completed.stderr)
        self.assertIn("bridge exploded", completed.stderr)

    def test_start_waits_for_socket_mode_readiness(self) -> None:
        self.write_executable(
            "uv",
            """#!/bin/sh
ready_file=
while [ "$#" -gt 0 ]; do
    if [ "$1" = "--ready-file" ]; then
        shift
        ready_file=$1
        break
    fi
    shift
done
trap 'rm -f "$ready_file"; exit 0' TERM INT
while :; do
    printf '%s %s %s\n' "$OPENTAG_PROCESS_ID" "$$" "$(date +%s)" >"$ready_file.tmp"
    mv "$ready_file.tmp" "$ready_file"
    sleep 1
done
""",
        )

        try:
            started = self.run_tag(
                "start", FAKE_CURL_HEALTHY="1", OPENTAG_STARTUP_ATTEMPTS="5"
            )
            status = self.run_tag("status", FAKE_CURL_HEALTHY="1")

            self.assertEqual(0, started.returncode, started.stderr)
            self.assertEqual(0, status.returncode, status.stderr)
            self.assertIn("Slack bridge: connected", status.stdout)
        finally:
            self.run_tag("stop", FAKE_CURL_HEALTHY="0")

    def test_stop_does_not_kill_process_when_pid_identity_does_not_match(self) -> None:
        sleeper = subprocess.Popen(["sleep", "30"])
        try:
            runtime = self.root / ".runtime"
            runtime.mkdir()
            (runtime / "slack.pid").write_text(
                f"{sleeper.pid}\nwrong start time\nslack_socket_agent.py\ninstance\n",
                encoding="utf-8",
            )

            completed = self.run_tag("stop")

            self.assertEqual(0, completed.returncode)
            time.sleep(0.05)
            self.assertIsNone(sleeper.poll())
            self.assertFalse((runtime / "slack.pid").exists())
        finally:
            sleeper.terminate()
            sleeper.wait(timeout=5)

    def test_stop_requires_matching_instance_id_for_managed_bridge(self) -> None:
        sleeper = subprocess.Popen(["sleep", "30"])
        try:
            started = subprocess.run(
                ["ps", "-p", str(sleeper.pid), "-o", "lstart="],
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            runtime = self.root / ".runtime"
            runtime.mkdir()
            (runtime / "slack.pid").write_text(
                f"{sleeper.pid}\n{started}\nsleep\nnot-in-command-line\n",
                encoding="utf-8",
            )

            completed = self.run_tag("stop")

            self.assertEqual(0, completed.returncode)
            time.sleep(0.05)
            self.assertIsNone(sleeper.poll())
        finally:
            sleeper.terminate()
            sleeper.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
