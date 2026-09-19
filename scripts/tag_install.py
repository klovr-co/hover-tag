#!/usr/bin/env python3
"""Install a verified release (or explicit development source) into TAG_HOME."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid
import zipfile
from pathlib import Path

REPOSITORY = "https://github.com/klovr-co/tag"

LEGACY_ADMIN_SKILL = (
    "---\nname: open-tag-admin\ndescription: Configure and diagnose this TAG installation.\n---\n"
    "Use `tag paths` to find this installation, `tag doctor` to check it, and "
    "`tag setup` for initial configuration. Settings are in config/settings.json. "
    "Use tag start, tag status, tag logs and tag stop for lifecycle management. "
    "Never print credentials. Ask the operator to authorize Slack and integration logins.\n"
)
ADMIN_SKILL = (
    "---\nname: open-tag-admin\ndescription: Set up, configure, and diagnose this Tag installation.\n---\n"
    "Run `tag paths` and read the file at `admin_skill` for this release's full workflow. "
    "Begin with `tag inspect --json`; ask only for missing information. "
    "Use `tag config init --json` for missing defaults and `tag config set` for targeted changes. "
    "Use stdin for secrets; never print credentials or place them in command arguments. "
    "Use `tag doctor --json` for diagnosis and `tag status --json` for service readiness. "
    "The operator authorizes Slack and backend logins. Verify a real Slack reply separately.\n"
)


def download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        if not response.url.startswith("https://"):
            raise ValueError("Download redirected away from HTTPS")
        return response.read()


def unpack_release(archive: Path, destination: Path) -> Path:
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            target = (destination / entry.filename).resolve()
            if not target.is_relative_to(destination.resolve()) or "\\" in entry.filename:
                raise ValueError("Unsafe release archive path")
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Release archive contains a symlink")
        bundle.extractall(destination)
    return destination


def fetch_release(version: str | None, destination: Path) -> Path:
    if version is None:
        metadata = json.loads(download("https://api.github.com/repos/klovr-co/tag/releases/latest"))
        version = metadata["tag_name"].removeprefix("v")
    version = version.removeprefix("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version):
        raise ValueError("Invalid release version")
    name = f"tag-{version}.zip"
    base = f"{REPOSITORY}/releases/download/v{version}"
    checksums = download(base + "/SHA256SUMS").decode("utf-8")
    expected = next((line.split()[0] for line in checksums.splitlines()
                     if len(line.split()) == 2 and line.split()[1] == name), None)
    data = download(base + "/" + name)
    if expected is None or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("TAG release checksum verification failed")
    archive = destination / name
    archive.write_bytes(data)
    source = unpack_release(archive, destination / "source")
    if (source / "VERSION").read_text().strip() != version:
        raise ValueError("Release version does not match requested version")
    return source


def atomic_text(path: Path, text: str, mode: int = 0o600) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def install(source: Path, home: Path, bin_dir: Path, *, dependencies: bool = True) -> Path:
    sys.path.insert(0, str(source / "scripts"))
    from tag_paths import initialize
    from release_check import validate_release
    errors = validate_release(source)
    if errors:
        raise ValueError("Invalid release: " + "; ".join(errors))
    command = bin_dir / ("tag.cmd" if os.name == "nt" else "tag")
    if command.is_symlink() or (command.exists() and "TAG managed launcher" not in command.read_text(encoding="utf-8", errors="replace")):
        raise RuntimeError(f"Refusing to replace unrelated command: {command}. Choose --bin-dir.")
    initialize(home)
    lock = home / "state/install.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError(f"An install is already in progress; interrupted installs leave {lock}")
    try:
        version = (source / "VERSION").read_text().strip()
        release = home / "releases" / f"{version}-{uuid.uuid4().hex[:12]}"
        # An allowlist prevents copying credentials, worktree metadata, or personal skills.
        release.mkdir()
        for name in ("scripts", "references", "docs"):
            shutil.copytree(source / name, release / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in ("VERSION", "LICENSE", "NOTICE", "README.md", "RELEASE.md", "SECURITY.md",
                     "SKILL.md", ".env.example", "requirements-runtime.txt", "slack-app-manifest.yaml",
                     "tag", "tag.cmd", "install.sh", "install.ps1"):
            shutil.copy2(source / name, release / name)
        (release / "tag").chmod(0o755)
        (release / "install.sh").chmod(0o755)
        python = release / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if dependencies:
            uv = shutil.which("uv")
            if not uv:
                raise RuntimeError("Install uv first: https://docs.astral.sh/uv/")
            subprocess.run([uv, "venv", "--python", sys.executable, str(release / ".venv")], check=True)
            subprocess.run([uv, "pip", "install", "--python", str(python), "-r", str(release / "requirements-runtime.txt")], check=True)
        else:
            # Explicit test/development mode; never advertised as a complete install.
            python = Path(sys.executable)
        for backend in (".agents", ".claude"):
            bundled = home / "workspace" / backend / "skills/open-tag-admin"
            skill = bundled / "SKILL.md"
            if not bundled.exists() or (skill.is_file() and skill.read_text(encoding="utf-8") == LEGACY_ADMIN_SKILL):
                bundled.mkdir(parents=True, exist_ok=True)
                skill.write_text(ADMIN_SKILL, encoding="utf-8")
        # Keep the launcher fixed while the pointer changes atomically on upgrade.
        launcher = home / "bin/tag-launch.py"
        launcher_text = '''# TAG managed launcher
import json, os, pathlib, subprocess, sys
home = pathlib.Path(__file__).resolve().parent.parent
record = json.loads((home / "current.json").read_text(encoding="utf-8"))
release = home / "releases" / record["release"]
os.environ["TAG_HOME"] = str(home)
raise SystemExit(subprocess.call([record["python"], str(release / "scripts/tag_cli.py"), *sys.argv[1:]]))
'''
        atomic_text(launcher, launcher_text)
        bin_dir.mkdir(parents=True, exist_ok=True)
        command = bin_dir / ("tag.cmd" if os.name == "nt" else "tag")
        if command.exists() and "TAG managed launcher" not in command.read_text(encoding="utf-8", errors="replace"):
            raise RuntimeError(f"Refusing to replace unrelated command: {command}")
        if os.name == "nt":
            if any(c in str(path) for path in (Path(sys.executable), launcher) for c in '%\r\n"'):
                raise ValueError("Windows launcher paths cannot contain percent signs, quotes or newlines")
            script = f'@rem TAG managed launcher\n@"{sys.executable}" "{launcher}" %*\n'
        else:
            script = f'#!/bin/sh\n# TAG managed launcher\nexec {shlex.quote(sys.executable)} {shlex.quote(str(launcher))} "$@"\n'
        atomic_text(command, script, 0o755)
        current = home / "current.json"
        if current.exists():
            atomic_text(home / "previous.json", current.read_text(encoding="utf-8"))
        atomic_text(current, json.dumps({"release": release.name, "python": str(python)}, indent=2) + "\n")
        print(f"Installed Tag v{version}\nTAG home: {home}\nCommand: {command}")
        print(f"Add {bin_dir} to your PATH if needed. Then run: tag setup")
        print("Upgrading a running instance? Run tag stop, then tag start to use the new release.")
        if not dependencies:
            print("Development install: dependencies were skipped.")
        return release
    finally:
        lock.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="install an explicit local source tree")
    parser.add_argument("--version", help="release version; defaults to latest stable release")
    parser.add_argument("--bin-dir", type=Path)
    parser.add_argument("--skip-dependencies", action="store_true", help="development/test installs only")
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        raise RuntimeError("Python 3.10 or newer is required")
    with tempfile.TemporaryDirectory(prefix="tag-install-") as temporary:
        source = args.source.resolve() if args.source else fetch_release(args.version, Path(temporary))
        sys.path.insert(0, str(source / "scripts"))
        from tag_paths import tag_home
        home = tag_home()
        bin_dir = args.bin_dir or (home / "bin" if os.name == "nt" else Path.home() / ".local/bin")
        install(source, home, bin_dir.resolve(), dependencies=not args.skip_dependencies)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Installation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
