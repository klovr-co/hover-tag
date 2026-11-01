"""Versioned, verified installation dependencies shared by setup and upgrades."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

SLACK_VERSION = "4.8.0"
# Immutable upstream release assets, not the mutable *_latest aliases.
SLACK_ARTIFACTS = {
    ("darwin", "arm64"): ("macOS_arm64", "56bae70638ce8fba568423fa408ab2e3da095bcef82d07904ff191535db415c5"),
    ("darwin", "x86_64"): ("macOS_amd64", "7c00a576e571e291ffb25710ecb6ffa7733455682eb879acd4f40812285fc55b"),
    ("linux", "arm64"): ("linux_arm64", "dbfc62385ac35d66aa3356d2a99ee1444bd05eafb7b14ef08235e408a2fae6cb"),
    ("linux", "x86_64"): ("linux_amd64", "533ebc242561a79c6aaf238c3417ce113d1257ace80cf90f1e5f852d8ec9ca7b"),
    ("win32", "x86_64"): ("windows_64-bit", "ccf7ac1fcdbf39cdbd66cbc04d3018c5c293b70e489a85e07b140693377c6d24"),
}
# Slack publishes no Windows ARM64 build; Windows 11 on ARM runs the x64 one
# under emulation, and ensure_slack still checks that it actually runs.
SLACK_ARTIFACTS[("win32", "arm64")] = SLACK_ARTIFACTS[("win32", "x86_64")]


def slack_executable() -> str:
    return "slack.exe" if sys.platform == "win32" else "slack"


def host_machine() -> str:
    return {"aarch64": "arm64", "amd64": "x86_64"}.get(platform.machine().lower(), platform.machine().lower())


SLACK_CONSOLE_HOST = Path(__file__).resolve().with_name("slack_console_host.py")


def slack_console_hosted() -> bool:
    """Whether captured Slack CLI runs go through slack_console_host.py.

    On Windows on ARM, the Slack CLI asks the terminal for its background color at
    startup and waits for the reply with no working timeout. A console with no
    window, such as the one Tag.app's CREATE_NO_WINDOW gives it, never replies, so
    the CLI hangs. With no console at all it doesn't ask, but then every hook it
    runs through PowerShell opens a new console window. The host gives it a
    windowless console that replies.
    """
    return sys.platform == "win32" and host_machine() == "arm64"


def run_slack(command: list, **options) -> subprocess.CompletedProcess:
    """``subprocess.run`` for every Slack CLI call whose output Tag captures.

    In a terminal, interactive sign-in runs the CLI directly instead, so it keeps
    the operator's terminal.
    """
    if slack_console_hosted():
        # The base interpreter, not a venv's launcher: the host needs only the
        # standard library, and the launcher's job would end whatever the CLI
        # leaves running on purpose (a browser) when the host finishes.
        python = getattr(sys, "_base_executable", None) or sys.executable
        command = [python, "-I", str(SLACK_CONSOLE_HOST), *map(str, command)]
        options["creationflags"] = options.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
    return subprocess.run(command, **options)


def slack_problem(command: str | Path) -> str | None:
    """Why the Slack CLI at ``command`` is unusable, or None when it runs a supported 4.x."""
    try:
        result = run_slack([str(command), "version", "--skip-update", "--no-color"],
                           capture_output=True, text=True, timeout=20, check=False,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return "it did not finish `slack version` within 20 seconds"
    except OSError as error:
        return f"it could not start ({error.strerror or error})"
    if result.returncode:
        return f"`slack version` exited with code {result.returncode}"
    match = re.search(r"\b(?:v)?(\d+)\.(\d+)\.(\d+)\b", result.stdout)
    if not match:
        return f"`slack version` printed no version: {result.stdout.strip()[:120]!r}"
    if not (4, 7, 0) <= tuple(map(int, match.groups())) < (5, 0, 0):
        return f"it is version {'.'.join(match.groups())}, not 4.7+ (4.x)"
    return None


def slack_compatible(command: str | Path) -> bool:
    return slack_problem(command) is None


def download_verified(url: str, destination: Path, digest: str) -> None:
    checksum = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": "tag-installer"})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as output:
        if not response.url.startswith("https://"):
            raise RuntimeError("Dependency download redirected away from HTTPS")
        received = 0
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            checksum.update(chunk)
            received += len(chunk)
            print(f"\rDownloading Slack CLI: {received // 1024} KiB", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    if checksum.hexdigest() != digest:
        raise RuntimeError("Slack CLI checksum mismatch; retry setup or upgrade")


def ensure_slack(home: Path) -> Path:
    existing = shutil.which("slack")
    if existing and slack_compatible(existing):
        return Path(existing).resolve()
    target = SLACK_ARTIFACTS.get((sys.platform, host_machine()))
    if target is None:
        raise RuntimeError("Automatic Slack CLI setup is unavailable on this platform; install Slack CLI 4.7+ (4.x) and retry")
    name = slack_executable()
    destination = home / "runtime/slack" / SLACK_VERSION / name
    if slack_compatible(destination):
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    artifact, digest = target
    suffix = "zip" if sys.platform == "win32" else "tar.gz"
    url = f"https://github.com/slackapi/slack-cli/releases/download/v{SLACK_VERSION}/slack_cli_{SLACK_VERSION}_{artifact}.{suffix}"
    # Extract only a regular executable: never archive paths or symlinks. Publish
    # only after validation; a killed download cannot become the selected CLI.
    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=destination.parent) as directory:
        temporary = Path(directory)
        archive = temporary / f"slack.{suffix}"
        download_verified(url, archive, digest)
        candidate = temporary / name
        extract_executable(archive, name, candidate)
        candidate.chmod(0o755)
        if problem := slack_problem(candidate):
            raise RuntimeError(f"Downloaded Slack CLI cannot run on this machine: {problem}. "
                               "Retry after checking OS compatibility")
        publish(candidate, destination)
    return destination


def publish(candidate: Path, destination: Path, attempts: int = 20) -> None:
    """Move a checked executable into place.

    Windows keeps a just-run executable open for a moment (antivirus scans,
    process teardown), so the move can fail with WinError 32. Retry briefly.
    """
    for attempt in range(attempts):
        try:
            os.replace(candidate, destination)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.25)


def extract_executable(archive: Path, name: str, candidate: Path) -> None:
    """Copy the single regular file called ``name`` out of a .tar.gz or .zip."""
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            members = [m for m in bundle.infolist() if not m.is_dir() and Path(m.filename).name == name]
            if len(members) != 1:
                raise RuntimeError("Slack archive must contain exactly one CLI executable")
            with bundle.open(members[0]) as source, candidate.open("wb") as output:
                shutil.copyfileobj(source, output)
        return
    with tarfile.open(archive, "r:gz") as bundle:
        members = [m for m in bundle.getmembers() if m.isfile() and Path(m.name).name == name]
        if len(members) != 1:
            raise RuntimeError("Slack archive must contain exactly one CLI executable")
        source = bundle.extractfile(members[0])
        if source is None:
            raise RuntimeError("Slack archive has no executable data")
        with candidate.open("wb") as output:
            shutil.copyfileobj(source, output)


def activate_slack(command: Path) -> None:
    # Preserve HOME and Slack's authorization/configuration directories.
    os.environ["PATH"] = str(command.parent) + os.pathsep + os.environ.get("PATH", "")


def prepare_python(source: Path, home: Path) -> tuple[Path, Path]:
    if sys.platform == "win32":
        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-File", str(source / "install.ps1"), "-RuntimeInfo"]
    else:
        command = ["sh", str(source / "install.sh"), "--runtime-info"]
    result = subprocess.run(command, env=dict(os.environ, TAG_HOME=str(home)), stdout=subprocess.PIPE,
                            encoding="utf-8", check=False)
    if result.returncode:
        raise RuntimeError("Tag runtime preparation failed; check the download error above and retry")
    python, uv = result.stdout.strip().splitlines()
    return Path(python), Path(uv)


def migrate(home: Path, source: Path) -> None:
    """Migration v1: repair installations upgraded by a pre-bootstrap installer.

    Older installers execute their own installation code against the new source.
    Prepare a new release through the new installer before dependent startup
    checks; leave the old pointer and runtime usable if preparation fails.
    """
    current = home / "current.json"
    if not current.is_file():
        # Source setup provisions Slack privately too. Its PATH update only
        # lives in that process, so restore it on later starts. Use ensure_slack
        # to verify/repair older or interrupted runtime installs on retry.
        if (home / "runtime/slack").is_dir():
            activate_slack(ensure_slack(home))
        return  # A fresh source checkout still provisions Slack during setup.
    record = json.loads(current.read_text(encoding="utf-8"))
    if record.get("dependency_schema", 0) < 1:
        # Includes Windows installs made with a system Python: they move to
        # Tag's private Python, and the system one is no longer needed.
        python, _ = prepare_python(source, home)
        result = subprocess.run(
            [str(python), str(source / "scripts/tag_install.py"), "--source", str(source),
             "--bin-dir", record["bin_dir"], "--migrate-dependencies"],
            env=dict(os.environ, TAG_HOME=str(home)), check=False,
        )
        if result.returncode:
            raise RuntimeError("Tag runtime migration failed; the active release was preserved. Retry startup to resume")
        updated = json.loads(current.read_text(encoding="utf-8"))
        if updated.get("dependency_schema") != 1:
            raise RuntimeError("Runtime migration did not finish; retry Tag startup")
        # Reload both the release code and recorded credentials through normal startup.
        command = [updated["python"], str(home / "releases" / updated["release"] / "scripts/tag_cli.py"), *sys.argv[1:]]
        if sys.platform == "win32":
            # Windows execv starts a new process and exits 0 at once; wait for its real result.
            sys.exit(subprocess.call(command))
        os.execv(updated["python"], command)
    command = ensure_slack(home)
    activate_slack(command)
    # Recheck actual executable even if a previous checkpoint exists.
    try:
        from tag_install import atomic_text
    except ImportError:
        from scripts.tag_install import atomic_text
    (home / "state").mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_text(home / "state/dependencies-v1.json", json.dumps({"version": 1, "slack": str(command), "python": record["python"]}) + "\n")
