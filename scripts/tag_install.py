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
import textwrap
import urllib.request
import uuid
import zipfile
from pathlib import Path

REPOSITORY = "https://github.com/klovr-co/tag"
ACCENT = "38;2;56;207;241"
MUTED = "90"
SUCCESS = "38;2;149;197;112"
ASCII_FALLBACK = str.maketrans({
    "✓": "+",
    "›": ">",
    "─": "-",
    "·": ".",
    "…": "...",
})

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


def color_available() -> bool:
    return (
        sys.stdout.isatty()
        and os.getenv("TERM", "") not in {"", "dumb"}
        and "NO_COLOR" not in os.environ
    )


def styled(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if color_available() else text


def terminal_text(text: str) -> str:
    """Return installer output the active stdout encoding can write."""
    encoding = getattr(sys.stdout, "encoding", None)
    if not encoding:
        return text
    try:
        text.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return text.translate(ASCII_FALLBACK)
    return text


def emit(text: str = "") -> None:
    print(terminal_text(text), flush=True)


def content_width() -> int:
    return max(12, min(72, shutil.get_terminal_size((80, 24)).columns - 4))


def short_path(value: Path) -> str:
    path = str(value)
    home = str(Path.home())
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def paragraph(text: str, code: str = "", *, indent: str = "  ") -> None:
    for line in textwrap.wrap(
        str(text),
        width=max(8, content_width() - len(indent) + 2),
        break_long_words=True,
        break_on_hyphens=False,
    ):
        emit(indent + (styled(line, code) if code else line))


def header(section: str, detail: str = "") -> None:
    emit()
    emit("  " + styled("tag", "1;" + ACCENT) + "  /  " + styled(section, MUTED))
    emit("  " + styled("─" * content_width(), MUTED))
    if detail:
        paragraph(detail, MUTED)


def section(label: str) -> None:
    emit()
    paragraph(label.upper(), MUTED)


def row(name: str, value: str, *, good: bool = True) -> None:
    marker = "✓" if good else "!"
    paragraph(f"{marker}  {name:<9} {value}", SUCCESS if good else "33", indent="    ")


def install_step(command: list[str], label: str) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        output = (completed.stderr or completed.stdout).strip().splitlines()
        detail = "\n".join(output[-20:])
        raise RuntimeError(f"{label} failed" + (f":\n{detail}" if detail else ""))


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


def command_owner(command: Path) -> str | None:
    """Identify launchers Tag may safely replace without claiming unrelated commands."""
    if command.is_symlink():
        try:
            target = command.resolve(strict=True)
            text = target.read_text(encoding="utf-8", errors="replace")
        except (OSError, RuntimeError):
            return None
        source = target.parent
        if (
            target.name in {"tag", "tag.cmd"}
            and (source / "VERSION").is_file()
            and (source / "scripts/tag_cli.py").is_file()
            and "scripts/tag_cli.py" in text.replace("\\", "/")
        ):
            return "legacy Tag source checkout"
        return None
    if command.is_file():
        try:
            if "TAG managed launcher" in command.read_text(
                encoding="utf-8", errors="replace"
            ):
                return "managed Tag installation"
        except OSError:
            pass
    return None


def install(source: Path, home: Path, bin_dir: Path, *, dependencies: bool = True) -> Path:
    scripts_dir = str(source / "scripts")
    sys.path.insert(0, scripts_dir)
    try:
        from tag_paths import initialize
        from release_check import validate_release
    finally:
        sys.path.remove(scripts_dir)
    errors = validate_release(source)
    if errors:
        raise ValueError("Invalid release: " + "; ".join(errors))
    command = bin_dir / ("tag.cmd" if os.name == "nt" else "tag")
    existing_owner = command_owner(command)
    legacy_command = str(command.resolve()) if existing_owner == "legacy Tag source checkout" else None
    if (command.exists() or command.is_symlink()) and existing_owner is None:
        raise RuntimeError(f"Refusing to replace unrelated command: {command}. Choose --bin-dir.")
    initialize(home)
    if legacy_command:
        atomic_text(
            home / "state/legacy-command.json",
            json.dumps({"command": legacy_command}, indent=2) + "\n",
        )
    lock = home / "state/install.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError(f"An install is already in progress; interrupted installs leave {lock}")
    try:
        version = (source / "VERSION").read_text().strip()
        header("Install", f"Preparing Tag v{version} in an isolated runtime.")
        section("Preparing")
        row("Release", f"Tag v{version}")
        if existing_owner == "legacy Tag source checkout":
            row("Migration", "Legacy command recognized; original checkout preserved")
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
            paragraph("Installing runtime dependencies…", MUTED, indent="    ")
            install_step(
                [uv, "venv", "--python", sys.executable, str(release / ".venv")],
                "Creating the Tag runtime",
            )
            install_step(
                [uv, "pip", "install", "--python", str(python), "-r", str(release / "requirements-runtime.txt")],
                "Installing Tag dependencies",
            )
            row("Runtime", f"Python {sys.version_info.major}.{sys.version_info.minor} · dependencies ready")
        else:
            # Explicit test/development mode; never advertised as a complete install.
            python = Path(sys.executable)
            row("Runtime", "Development mode · dependencies skipped")
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
        if (command.exists() or command.is_symlink()) and command_owner(command) is None:
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
        row("Command", short_path(command))
        row("Home", short_path(home))
        emit()
        emit("  " + styled("─" * content_width(), MUTED))
        paragraph("✓  Tag is installed", "1;" + SUCCESS)
        paragraph("Configuration and personal workspace data were preserved.", MUTED)
        emit()
        paragraph("Next step", MUTED)
        paragraph("› tag setup", "1;" + ACCENT)
        paragraph("Already configured? Run tag stop, then tag start.", MUTED)
        paragraph(f"If needed, add {short_path(bin_dir)} to PATH.", MUTED)
        emit()
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
