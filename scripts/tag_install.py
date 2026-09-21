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
import urllib.error
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

API_RELEASES = "https://api.github.com/repos/klovr-co/tag/releases"
CHANNELS = ("stable", "beta", "alpha", "edge")
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta)(?:\.(\d+))?)?$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
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
    "Use `tag upgrade --dry-run --json` to check for updates and `tag upgrade` to apply one. "
    "The operator authorizes Slack and backend logins. Verify a real Slack reply separately.\n"
)


@dataclass(frozen=True)
class ReleaseSelection:
    channel: str
    version: str
    commit_sha: str
    selector: str = "channel"


@dataclass(frozen=True)
class FetchedRelease:
    source: Path
    selection: ReleaseSelection


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
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "tag-installer",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            if not response.url.startswith("https://"):
                raise ValueError("Download redirected away from HTTPS")
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code == 429 or (
            error.code == 403 and error.headers.get("X-RateLimit-Remaining") == "0"
        ):
            raise RuntimeError("GitHub API rate limit exceeded; try again later") from error
        raise RuntimeError(f"Download failed with HTTP {error.code}: {url}") from error


def _json_download(url: str) -> Any:
    try:
        return json.loads(download(url).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"GitHub returned malformed JSON for {url}") from error


def _parsed_version(value: str) -> tuple[tuple[int, int, int, int, int], str]:
    match = VERSION_RE.fullmatch(value.removeprefix("v"))
    if not match:
        raise ValueError(f"Invalid release version: {value}")
    major, minor, patch, phase, number = match.groups()
    normalized_phase = phase or "stable"
    phase_rank = {"alpha": 0, "beta": 1, "stable": 2}[normalized_phase]
    return (
        (int(major), int(minor), int(patch), phase_rank, int(number or 0)),
        normalized_phase,
    )


def release_version_key(value: str) -> tuple[int, int, int, int, int]:
    """Return Tag's sortable semantic release key."""
    return _parsed_version(value)[0]


def _release_matches_channel(release: dict[str, Any], channel: str) -> bool:
    if release.get("draft") or not isinstance(release.get("tag_name"), str):
        return False
    try:
        _, phase = _parsed_version(release["tag_name"])
    except ValueError:
        return False
    if bool(release.get("prerelease")) != (phase != "stable"):
        return False
    allowed = {
        "stable": {"stable"},
        "beta": {"stable", "beta"},
        "alpha": {"stable", "beta", "alpha"},
    }
    return phase in allowed[channel]


def resolve_channel(channel: str) -> dict[str, Any]:
    if channel not in CHANNELS:
        raise ValueError(f"Unknown release channel: {channel}")
    if channel == "edge":
        release = _json_download(API_RELEASES + "/tags/edge")
        if (
            not isinstance(release, dict)
            or release.get("tag_name") != "edge"
            or release.get("draft")
            or not release.get("prerelease")
        ):
            raise ValueError("The edge channel is not a published prerelease")
        return release
    releases: list[dict[str, Any]] = []
    for page in range(1, 101):
        payload = _json_download(f"{API_RELEASES}?per_page=100&page={page}")
        if not isinstance(payload, list):
            raise ValueError("GitHub releases response is not a list")
        releases.extend(item for item in payload if isinstance(item, dict))
        if len(payload) < 100:
            break
    else:
        raise RuntimeError("GitHub release pagination exceeded 100 pages")
    matches = [release for release in releases if _release_matches_channel(release, channel)]
    if not matches:
        raise RuntimeError(f"No published releases are available for channel {channel}")
    return max(matches, key=lambda item: _parsed_version(item["tag_name"])[0])


def resolve_version(version: str) -> tuple[dict[str, Any], str, str]:
    normalized = version.removeprefix("v")
    _, phase = _parsed_version(normalized)
    release = _json_download(API_RELEASES + "/tags/v" + normalized)
    if not isinstance(release, dict) or release.get("tag_name") != "v" + normalized:
        raise ValueError("GitHub returned the wrong release for the requested version")
    if release.get("draft"):
        raise ValueError("The requested release is still a draft")
    if bool(release.get("prerelease")) != (phase != "stable"):
        raise ValueError("The requested release type does not match its version")
    return release, normalized, phase


def _asset_url(release: dict[str, Any], name: str) -> str:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ValueError("Release metadata does not contain an asset list")
    matches = [
        asset.get("browser_download_url") for asset in assets
        if isinstance(asset, dict) and asset.get("name") == name
    ]
    if len(matches) != 1 or not isinstance(matches[0], str) or not matches[0].startswith("https://"):
        raise ValueError(f"Release must contain exactly one valid {name} asset")
    return matches[0]


def _verify_provenance(
    data: bytes, *, expected_channel: str, archive_name: str,
    digest: str, version: str,
) -> str:
    try:
        provenance = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Release provenance is malformed") from error
    if not isinstance(provenance, dict):
        raise ValueError("Release provenance must be a JSON object")
    expected = {
        "schema_version": 1,
        "channel": expected_channel,
        "source_ref": "refs/heads/main",
        "version": version,
        "archive": {"name": archive_name, "sha256": digest},
    }
    for key, value in expected.items():
        if provenance.get(key) != value:
            raise ValueError(f"Release provenance {key} does not match the selected release")
    commit_sha = provenance.get("commit_sha")
    if not isinstance(commit_sha, str) or not SHA_RE.fullmatch(commit_sha):
        raise ValueError("Release provenance contains an invalid commit SHA")
    built_at = provenance.get("built_at")
    try:
        timestamp = datetime.fromisoformat(built_at.removesuffix("Z") + "+00:00")
        valid_timestamp = built_at.endswith("Z") and timestamp.utcoffset() == timezone.utc.utcoffset(None)
    except (AttributeError, TypeError, ValueError):
        valid_timestamp = False
    if not valid_timestamp:
        raise ValueError("Release provenance contains an invalid build timestamp")
    return commit_sha


def _default_channel() -> str:
    script = Path(__file__).resolve()
    candidates = (script.parent / "release-channels.json", script.parents[1] / "release-channels.json")
    policy_path = next((path for path in candidates if path.is_file()), None)
    if policy_path is None:
        raise RuntimeError("release-channels.json is missing from the installer bootstrap")
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("release-channels.json is malformed") from error
    channel = policy.get("default_channel") if isinstance(policy, dict) else None
    if not isinstance(policy, dict) or policy.get("schema_version") != 1 or channel not in CHANNELS:
        raise ValueError("release-channels.json contains an unsupported policy")
    return channel


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


def fetch_release(
    version: str | None, destination: Path, channel: str | None = None,
) -> FetchedRelease:
    destination.mkdir(parents=True, exist_ok=True)
    if version is not None and channel is not None:
        raise ValueError("Choose either --channel or --version, not both")
    if version is not None:
        release, version, selected_channel = resolve_version(version)
        provenance_channel = "release"
        name = f"tag-{version}.zip"
        selector = "version"
    else:
        selected_channel = channel or _default_channel()
        selector = "channel"
        release = resolve_channel(selected_channel)
        if selected_channel == "edge":
            name = "tag-edge.zip"
            version = ""
            provenance_channel = "edge"
        else:
            version = release["tag_name"].removeprefix("v")
            name = f"tag-{version}.zip"
            provenance_channel = "release"
    checksums_data = download(_asset_url(release, "SHA256SUMS"))
    provenance_data = download(_asset_url(release, "BUILD-PROVENANCE.json"))
    data = download(_asset_url(release, name))
    try:
        checksums = checksums_data.decode("utf-8")
    except UnicodeError as error:
        raise ValueError("Release checksum manifest is malformed") from error
    checksum_matches = [
        parts[0] for line in checksums.splitlines()
        if len(parts := line.split()) == 2 and parts[1] == name and re.fullmatch(r"[0-9a-f]{64}", parts[0])
    ]
    digest = hashlib.sha256(data).hexdigest()
    if checksum_matches != [digest]:
        raise ValueError("Tag release checksum verification failed")
    if selected_channel == "edge":
        try:
            edge_provenance = json.loads(provenance_data.decode("utf-8"))
            version = edge_provenance.get("version", "") if isinstance(edge_provenance, dict) else ""
        except (UnicodeError, json.JSONDecodeError):
            version = ""
        _parsed_version(version)
    commit_sha = _verify_provenance(
        provenance_data, expected_channel=provenance_channel,
        archive_name=name, digest=digest, version=version,
    )
    if provenance_channel == "release" and release.get("target_commitish") != commit_sha:
        raise ValueError("Release provenance commit does not match the GitHub release target")
    archive = destination / name
    archive.write_bytes(data)
    source = unpack_release(archive, destination / "source")
    if (source / "VERSION").read_text().strip() != version:
        raise ValueError("Release version does not match requested version")
    return FetchedRelease(
        source,
        ReleaseSelection(selected_channel, version, commit_sha, selector),
    )


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


def install(
    source: Path, home: Path, bin_dir: Path, *, dependencies: bool = True,
    selection: ReleaseSelection | None = None,
) -> Path:
    scripts_dir = str(source / "scripts")
    sys.path.insert(0, scripts_dir)
    try:
        from tag_paths import initialize
        import tag_instances
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
    tag_instances.ensure_default(home)
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
        if selection is not None and selection.version != version:
            raise ValueError("Selected release metadata does not match the installed source")
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
                     "tag", "tag.cmd", "install.sh", "install.ps1", "release-channels.json"):
            shutil.copy2(source / name, release / name)
        (release / "tag").chmod(0o755)
        (release / "install.sh").chmod(0o755)
        python = release / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if dependencies:
            uv = shutil.which("uv")
            paragraph("Installing runtime dependencies…", MUTED, indent="    ")
            if uv:
                install_step(
                    [uv, "venv", "--python", sys.executable, str(release / ".venv")],
                    "Creating the Tag runtime",
                )
                install_step(
                    [uv, "pip", "install", "--python", str(python), "-r", str(release / "requirements-runtime.txt")],
                    "Installing Tag dependencies",
                )
            else:
                install_step(
                    [sys.executable, "-m", "venv", str(release / ".venv")],
                    "Creating the Tag runtime with Python venv",
                )
                install_step(
                    [str(python), "-m", "pip", "install", "--disable-pip-version-check",
                     "-r", str(release / "requirements-runtime.txt")],
                    "Installing Tag dependencies with pip",
                )
            row("Runtime", f"Python {sys.version_info.major}.{sys.version_info.minor} · dependencies ready")
        else:
            # Explicit test/development mode; never advertised as a complete install.
            python = Path(sys.executable)
            row("Runtime", "Development mode · dependencies skipped")
        instance_homes = [Path(str(item["home"])) for item in tag_instances.discover(home)
                          if item.get("valid")]
        for instance in instance_homes:
            for backend in (".agents", ".claude"):
                bundled = instance / "workspace" / backend / "skills/open-tag-admin"
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
        current_record: dict[str, Any] = {
            "release": release.name,
            "python": str(python),
            "bin_dir": str(bin_dir.resolve()),
            "installed_version": version,
        }
        if selection is not None:
            current_record.update({
                "channel": selection.channel,
                "selection": selection.selector,
                "installed_commit": selection.commit_sha,
                "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            })
        atomic_text(current, json.dumps(current_record, indent=2, sort_keys=True) + "\n")
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
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--channel", choices=CHANNELS, help="release update channel")
    selector.add_argument("--version", help="exact immutable release version")
    parser.add_argument("--bin-dir", type=Path)
    parser.add_argument("--skip-dependencies", action="store_true", help="development/test installs only")
    args = parser.parse_args()
    if args.source and (args.channel or args.version):
        parser.error("--source cannot be combined with --channel or --version")
    if sys.version_info < (3, 10):
        raise RuntimeError("Python 3.10 or newer is required")
    with tempfile.TemporaryDirectory(prefix="tag-install-") as temporary:
        selection = None
        if args.source:
            source = args.source.resolve()
        else:
            fetched = fetch_release(args.version, Path(temporary), args.channel)
            source, selection = fetched.source, fetched.selection
        sys.path.insert(0, str(source / "scripts"))
        from tag_paths import tag_home
        home = tag_home()
        bin_dir = args.bin_dir or (home / "bin" if os.name == "nt" else Path.home() / ".local/bin")
        install(
            source, home, bin_dir.resolve(), dependencies=not args.skip_dependencies,
            selection=selection,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Installation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
