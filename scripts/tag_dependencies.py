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
import urllib.request
from pathlib import Path

SLACK_VERSION = "4.8.0"
# Immutable upstream release assets, not the mutable *_latest aliases.
SLACK_ARTIFACTS = {
    ("darwin", "arm64"): ("macOS_arm64", "56bae70638ce8fba568423fa408ab2e3da095bcef82d07904ff191535db415c5"),
    ("darwin", "x86_64"): ("macOS_amd64", "7c00a576e571e291ffb25710ecb6ffa7733455682eb879acd4f40812285fc55b"),
    ("linux", "arm64"): ("linux_arm64", "dbfc62385ac35d66aa3356d2a99ee1444bd05eafb7b14ef08235e408a2fae6cb"),
    ("linux", "x86_64"): ("linux_amd64", "533ebc242561a79c6aaf238c3417ce113d1257ace80cf90f1e5f852d8ec9ca7b"),
}


def slack_compatible(command: str | Path) -> bool:
    try:
        result = subprocess.run([str(command), "version", "--skip-update", "--no-color"],
                                capture_output=True, text=True, timeout=20, check=False)
        match = re.search(r"\b(?:v)?(\d+)\.(\d+)\.(\d+)\b", result.stdout)
        return result.returncode == 0 and bool(match) and (4, 7, 0) <= tuple(map(int, match.groups())) < (5, 0, 0)
    except (OSError, subprocess.TimeoutExpired):
        return False


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
    machine = {"aarch64": "arm64", "amd64": "x86_64"}.get(platform.machine().lower(), platform.machine().lower())
    target = SLACK_ARTIFACTS.get((sys.platform, machine))
    if target is None:
        raise RuntimeError("Automatic Slack CLI setup is unavailable on this platform; install Slack CLI 4.7+ (4.x) and retry")
    destination = home / "runtime/slack" / SLACK_VERSION / "slack"
    if slack_compatible(destination):
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    artifact, digest = target
    url = f"https://github.com/slackapi/slack-cli/releases/download/v{SLACK_VERSION}/slack_cli_{SLACK_VERSION}_{artifact}.tar.gz"
    # Extract only a regular executable: never archive paths or symlinks. Publish
    # only after validation; a killed download cannot become the selected CLI.
    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=destination.parent) as directory:
        temporary = Path(directory)
        archive = temporary / "slack.tar.gz"
        download_verified(url, archive, digest)
        with tarfile.open(archive, "r:gz") as bundle:
            members = [m for m in bundle.getmembers() if m.isfile() and Path(m.name).name == "slack"]
            if len(members) != 1:
                raise RuntimeError("Slack archive must contain exactly one CLI executable")
            source = bundle.extractfile(members[0])
            if source is None:
                raise RuntimeError("Slack archive has no executable data")
            candidate = temporary / "slack"
            with candidate.open("wb") as output:
                shutil.copyfileobj(source, output)
        candidate.chmod(0o755)
        if not slack_compatible(candidate):
            raise RuntimeError("Downloaded Slack CLI cannot run on this machine; retry after checking OS compatibility")
        os.replace(candidate, destination)
    return destination


def activate_slack(command: Path) -> None:
    # Preserve HOME and Slack's authorization/configuration directories.
    os.environ["PATH"] = str(command.parent) + os.pathsep + os.environ.get("PATH", "")


def prepare_python(source: Path, home: Path) -> tuple[Path, Path]:
    result = subprocess.run(["sh", str(source / "install.sh"), "--runtime-info"],
                            env=dict(os.environ, TAG_HOME=str(home)), stdout=subprocess.PIPE,
                            text=True, check=False)
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
    record = json.loads(current.read_text())
    if record.get("dependency_schema", 0) < 1:
        if os.name == "nt":
            return  # Native Windows retains its existing Python prerequisite.
        python, _ = prepare_python(source, home)
        result = subprocess.run(
            [str(python), str(source / "scripts/tag_install.py"), "--source", str(source),
             "--bin-dir", record["bin_dir"], "--migrate-dependencies"],
            env=dict(os.environ, TAG_HOME=str(home)), check=False,
        )
        if result.returncode:
            raise RuntimeError("Tag runtime migration failed; the active release was preserved. Retry startup to resume")
        updated = json.loads(current.read_text())
        if updated.get("dependency_schema") != 1:
            raise RuntimeError("Runtime migration did not finish; retry Tag startup")
        # Reload both the release code and recorded credentials through normal startup.
        os.execv(updated["python"], [updated["python"], str(home / "releases" / updated["release"] / "scripts/tag_cli.py"), *sys.argv[1:]])
    command = ensure_slack(home)
    activate_slack(command)
    # Recheck actual executable even if a previous checkpoint exists.
    try:
        from tag_install import atomic_text
    except ImportError:
        from scripts.tag_install import atomic_text
    (home / "state").mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_text(home / "state/dependencies-v1.json", json.dumps({"version": 1, "slack": str(command), "python": record["python"]}) + "\n")
