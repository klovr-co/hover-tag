"""Exercise an installed release from outside the development checkout."""
import json
import os
import subprocess
import sys
import tempfile
import zipfile

from package_release import build_archive
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def stop_leftovers(directory: Path) -> None:
    """Report and stop processes still running from this test's Tag folder (Windows locks open files)."""
    if os.name != "nt":
        return
    script = ("$p = Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like $env:TAG_SMOKE_DIR + '*' };"
              " $p | ForEach-Object { Write-Output ($_.ProcessId.ToString() + ' ' + $_.CommandLine) };"
              " $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True,
                                text=True, env=dict(os.environ, TAG_SMOKE_DIR=str(directory.resolve())),
                                check=False, timeout=60)
    except subprocess.TimeoutExpired:
        print("Could not list leftover processes within 60 seconds; continuing.")
        return  # Best effort: never let cleanup hide the test result.
    if result.stdout.strip():
        print("Processes left running by Tag (stopped):\n" + result.stdout.strip())


with tempfile.TemporaryDirectory(prefix="Tag smoke ") as temporary:
    directory = Path(temporary)
    env = dict(os.environ, TAG_HOME=str(directory / "home"))
    # Exercise the shipped payload, not a checkout that can mask omitted files.
    archive = directory / "release.zip"
    source = directory / "release"
    build_archive(ROOT, archive)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(source)
        for entry in bundle.infolist():
            if entry.external_attr >> 16 & 0o111:
                (source / entry.filename).chmod(0o755)
    # The real bootstraps: neither needs a system Python.
    installer = (["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                  "-File", str(source / "install.ps1"), "-BinDir"]
                 if os.name == "nt" else ["sh", str(source / "install.sh"), "--bin-dir"])
    subprocess.run([*installer, str(directory / "bin")], env=env, check=True)
    command = directory / "bin" / ("tag.cmd" if os.name == "nt" else "tag")
    subprocess.run([str(command), "version"], cwd=directory, env=env, check=True)
    subprocess.run([str(command), "config", "init", "--json"], cwd=directory, env=env, check=True)
    for key, value in {
        "OPENTAG_BACKEND": "codex",
        "SLACK_ALLOWED_USER_IDS": "UOWNER",
    }.items():
        subprocess.run(
            [str(command), "config", "set", key, value, "--json"],
            cwd=directory,
            env=env,
            check=True,
        )
    subprocess.run([str(command), "doctor", "--offline"], cwd=directory, env=env, check=True)
    current = json.loads((directory / "home/current.json").read_text(encoding="utf-8"))
    subprocess.run([current["python"], "-c", "import slack_bolt, psutil, dulwich"], check=True)
    assert current.get("dependency_schema") == 1, current
    # Tag runs on its own Python, never the one that started this script.
    assert Path(current["python"]).resolve() != Path(sys.executable).resolve()
    slack = directory / "home/runtime/slack"
    if not any(slack.rglob("slack.exe" if os.name == "nt" else "slack")):
        import shutil
        assert shutil.which("slack"), "Slack CLI was neither provisioned nor already installed"
    stop_leftovers(directory)
