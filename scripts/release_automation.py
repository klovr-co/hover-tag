#!/usr/bin/env python3
"""Validate release candidates, remote gates, and edge build provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta)(?:\.(\d+))?)?$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAINTENANCE_BRANCH_RE = re.compile(r"^release/v(\d+)\.(\d+)\.x$")
REQUIRED_WORKFLOWS = ("ci.yml", "install-smoke.yml")
EDGE_WORKFLOW = "edge-build.yml"
AUTO_RELEASE_LABELS = {
    "release:next-patch", "release:next-minor", "release:skip",
}
SEMVER_CHANNELS = ("stable", "beta", "alpha")
DESKTOP_CHECKSUMS = frozenset(
    f"DESKTOP-SHA256SUMS-{platform}" for platform in ("macos", "windows", "linux")
)


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    phase: str = "stable"
    number: int | None = None

    @classmethod
    def parse(cls, value: str) -> "Version":
        match = VERSION_RE.fullmatch(value)
        if not match:
            raise ValueError(f"unsupported release version: {value!r}")
        major, minor, patch, phase, number = match.groups()
        return cls(int(major), int(minor), int(patch), phase or "stable", int(number) if number else None)

    @property
    def core(self) -> tuple[int, int, int]:
        return self.major, self.minor, self.patch

    def precedence(self) -> tuple[int, int, int, int, int]:
        phase_rank = {"alpha": 0, "beta": 1, "stable": 2}[self.phase]
        return (*self.core, phase_rank, self.number or 0)

    def __str__(self) -> str:
        suffix = "" if self.phase == "stable" else f"-{self.phase}"
        if self.number is not None:
            suffix += f".{self.number}"
        return f"{self.major}.{self.minor}.{self.patch}{suffix}"


def derive_next_version(previous: Version, phase: str, maintenance: bool = False) -> Version:
    if phase not in {"alpha", "beta", "stable"}:
        raise ValueError(f"unknown release phase: {phase}")
    if previous.phase == "stable":
        if phase != "alpha":
            raise ValueError("a released line must begin its next candidate with alpha")
        core = (previous.major, previous.minor, previous.patch + 1) if maintenance else (
            previous.major, previous.minor + 1, 0
        )
        return Version(*core, "alpha", 1)
    if phase == previous.phase:
        return Version(*previous.core, phase, (previous.number or 0) + 1)
    if previous.phase == "alpha" and phase == "beta":
        return Version(*previous.core, "beta", 1)
    if phase == "stable":
        return Version(*previous.core)
    raise ValueError(f"cannot move from {previous.phase} to {phase}")


def validate_transition(previous: Version, candidate: Version) -> list[str]:
    errors: list[str] = []
    if candidate.precedence() <= previous.precedence():
        return [f"candidate {candidate} must be newer than {previous}"]
    if candidate.core == previous.core:
        try:
            expected = derive_next_version(previous, candidate.phase)
        except ValueError as error:
            return [str(error)]
        if candidate != expected:
            errors.append(f"invalid transition from {previous}: expected {expected}, got {candidate}")
        return errors

    allowed_core = (
        (previous.major, previous.minor, previous.patch + 1),
        (previous.major, previous.minor + 1, 0),
    )
    if candidate.core not in allowed_core:
        errors.append("a new version line must be the next patch or next minor")
    if candidate.phase != "alpha":
        errors.append("a new version line must begin with an alpha candidate")
    legacy_alpha = candidate.major == 0 and candidate.minor == 1 and candidate.number is None
    if candidate.number != 1 and not legacy_alpha:
        errors.append("a new version line must begin with alpha.1")
    return errors


def validate_candidate(
    version: str, phase: str, tags: Iterable[str], allow_existing_version: bool = False
) -> list[str]:
    errors: list[str] = []
    try:
        candidate = Version.parse(version)
    except ValueError as error:
        return [str(error)]
    if candidate.phase != phase:
        errors.append(f"VERSION phase is {candidate.phase}, not requested phase {phase}")
    published: list[Version] = []
    for tag in tags:
        normalized = tag.removeprefix("v")
        try:
            published.append(Version.parse(normalized))
        except ValueError:
            continue
    if candidate in published and not allow_existing_version:
        errors.append(f"release tag v{candidate} already exists")
    transition_history = [item for item in published if item != candidate] if allow_existing_version else published
    if transition_history:
        errors.extend(validate_transition(max(transition_history, key=Version.precedence), candidate))
    return errors


def validate_maintenance_candidate(
    version: str, line: str, tags: Iterable[str], allow_existing_version: bool = False
) -> list[str]:
    """Validate a stable patch release made from a maintenance branch.

    `line` is "MAJOR.MINOR". Patch releases are stable only, and each must be
    the next patch after the newest published release of the same line.
    """
    try:
        candidate = Version.parse(version)
        major, minor = (int(part) for part in line.split("."))
    except ValueError as error:
        return [str(error)]
    errors: list[str] = []
    if (candidate.major, candidate.minor) != (major, minor):
        errors.append(f"VERSION {candidate} is not on the {line} line")
    if candidate.phase != "stable":
        errors.append("a maintenance branch publishes stable patch releases only")
    if errors:
        return errors
    published: list[Version] = []
    for tag in tags:
        try:
            item = Version.parse(tag.removeprefix("v"))
        except ValueError:
            continue
        if (item.major, item.minor) == (major, minor) and item.phase == "stable":
            published.append(item)
    if candidate in published and not allow_existing_version:
        return [f"release tag v{candidate} already exists"]
    previous = [item for item in published if item != candidate]
    newest = max((item.patch for item in previous), default=None)
    if newest is None:
        return [f"no stable release of the {line} line has been published"]
    if candidate.patch != newest + 1:
        return [f"expected {major}.{minor}.{newest + 1} after {major}.{minor}.{newest}, got {candidate}"]
    return []


def validate_source_ref(source_ref: str, version: str) -> list[str]:
    """Allow `main`, or the maintenance branch of the version's own line."""
    if source_ref == "refs/heads/main":
        return []
    match = MAINTENANCE_BRANCH_RE.fullmatch(source_ref.removeprefix("refs/heads/"))
    try:
        core = Version.parse(version).core
    except ValueError as error:
        return [str(error)]
    if match and source_ref.startswith("refs/heads/") and (
        int(match[1]), int(match[2])
    ) == core[:2]:
        return []
    return [f"invalid provenance source_ref: {source_ref!r} cannot build {version}"]


def validate_release_tag(source_version: str, release_tag: str) -> list[str]:
    """Require a release tag to describe the version selected in source."""
    try:
        source = Version.parse(source_version)
        tagged = Version.parse(release_tag.removeprefix("v"))
    except ValueError as error:
        return [str(error)]

    if tagged.phase in {"alpha", "beta"} and tagged.number is not None:
        if source.phase == tagged.phase and source.core == tagged.core:
            return []
    elif source == tagged:
        return []
    return [
        f"release tag {release_tag} does not match source VERSION {source_version}"
    ]


def select_auto_prerelease(
    base_version: str, tags: Iterable[str], labels: Iterable[str]
) -> Version | None:
    """Select the next automatic alpha or beta, or skip publication."""
    base = Version.parse(base_version)
    supplied_labels = set(labels)
    unknown_labels = supplied_labels - AUTO_RELEASE_LABELS
    if unknown_labels:
        raise ValueError(
            "unknown automatic release labels: " + ", ".join(sorted(unknown_labels))
        )
    selected_labels = supplied_labels & AUTO_RELEASE_LABELS
    if len(selected_labels) > 1:
        raise ValueError(
            "conflicting automatic release labels: " + ", ".join(sorted(selected_labels))
        )
    if "release:skip" in selected_labels:
        return None
    if base.phase == "stable" and not selected_labels:
        return None

    if base.phase == "beta" and selected_labels:
        raise ValueError("a beta line only supports the release:skip label")

    published: list[Version] = []
    for tag in tags:
        try:
            published.append(Version.parse(tag.removeprefix("v")))
        except ValueError:
            continue

    previous = max(published, key=Version.precedence, default=None)
    if base.phase == "beta":
        if previous is None:
            raise ValueError("a beta line requires a published alpha candidate")
        existing_numbers = [
            item.number or 0
            for item in published
            if item.core == base.core and item.phase == "beta"
        ]
        selected = Version(*base.core, "beta", max(existing_numbers, default=0) + 1)
        errors = validate_transition(previous, selected)
        if errors:
            raise ValueError("; ".join(errors))
        return selected

    if previous is None:
        core = base.core
    elif "release:next-patch" in selected_labels:
        core = max((previous.major, previous.minor, previous.patch + 1), base.core)
    elif "release:next-minor" in selected_labels:
        core = max((previous.major, previous.minor + 1, 0), base.core)
    elif base.core > previous.core:
        core = base.core
    elif previous.phase == "alpha" and previous.number is not None:
        core = previous.core
    else:
        core = (previous.major, previous.minor + 1, 0)

    existing_numbers = [
        item.number or 0
        for item in published
        if item.core == core and item.phase == "alpha"
    ]
    return Version(*core, "alpha", max(existing_numbers, default=0) + 1)


def _release_asset_names(release: dict[str, Any]) -> set[str]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        return set()
    return {
        asset["name"] for asset in assets
        if isinstance(asset, dict) and isinstance(asset.get("name"), str)
    }


def _channel_entry(release: dict[str, Any], version: str) -> dict[str, str]:
    tag = release.get("tag_name")
    sha = release.get("target_commitish")
    if not isinstance(tag, str) or not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        raise ValueError(f"release {tag!r} does not target a full commit SHA")
    archive_name = "tag-edge.zip" if tag == "edge" else f"tag-{version}.zip"
    required_assets = {archive_name, "SHA256SUMS", "BUILD-PROVENANCE.json"}
    missing = required_assets - _release_asset_names(release)
    if missing:
        raise ValueError(
            f"release {tag} is missing required assets: {', '.join(sorted(missing))}"
        )
    return {"version": version, "tag": tag, "commit_sha": sha}


def build_channel_index(
    releases: Iterable[dict[str, Any]], *, repository: str, generated_at: str
) -> dict[str, Any]:
    """Build public channel pointers from published, immutable releases."""
    published = [
        release for release in releases
        if isinstance(release, dict) and not release.get("draft")
    ]
    parsed: list[tuple[Version, dict[str, Any]]] = []
    for release in published:
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not tag.startswith("v"):
            continue
        try:
            version = Version.parse(tag[1:])
        except ValueError:
            continue
        if bool(release.get("prerelease")) != (version.phase != "stable"):
            continue
        parsed.append((version, release))

    # Once Tag.app has shipped, a newer release is complete only after every
    # platform build is attached. Until then its channel keeps the previous
    # release, so `tag upgrade` never moves past the Tag.app update feed.
    first_desktop = min(
        (version.precedence() for version, release in parsed
         if DESKTOP_CHECKSUMS <= _release_asset_names(release)),
        default=None,
    )
    if first_desktop is not None:
        parsed = [
            (version, release) for version, release in parsed
            if version.precedence() <= first_desktop
            or DESKTOP_CHECKSUMS <= _release_asset_names(release)
        ]

    channels: dict[str, dict[str, str]] = {}
    for channel in SEMVER_CHANNELS:
        candidates = [item for item in parsed if item[0].phase == channel]
        if candidates:
            version, release = max(candidates, key=lambda item: item[0].precedence())
            channels[channel] = _channel_entry(release, str(version))

    edge = next((release for release in published if release.get("tag_name") == "edge"), None)
    if edge is not None:
        if not edge.get("prerelease"):
            raise ValueError("edge release is not marked as a prerelease")
        channels["edge"] = _channel_entry(edge, "edge")

    return {
        "schema_version": 1,
        "repository": repository,
        "generated_at": generated_at,
        "channels": channels,
    }


def write_channel_index(repository: str, output: Path) -> None:
    releases: list[dict[str, Any]] = []
    for page in range(1, 101):
        payload = _github_json(repository, "releases", {
            "per_page": "100", "page": str(page),
        })
        if not isinstance(payload, list):
            raise ValueError("GitHub releases response is not a list")
        releases.extend(item for item in payload if isinstance(item, dict))
        if len(payload) < 100:
            break
    else:
        raise RuntimeError("GitHub release pagination exceeded 100 pages")
    generated_at = (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    index = build_channel_index(
        releases, repository=repository, generated_at=generated_at
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def validate_selected_sha(
    sha: str, resolved: str, is_on_main: bool, branch: str = "origin/main"
) -> list[str]:
    errors: list[str] = []
    if not SHA_RE.fullmatch(sha):
        errors.append("selected commit must be a full, lowercase 40-character SHA")
    if resolved != sha:
        errors.append("selected commit did not resolve to the requested SHA")
    if not is_on_main:
        errors.append(f"selected commit is not reachable from {branch}")
    return errors


def workflow_gate_state(runs: Iterable[dict[str, Any]], sha: str) -> str:
    relevant = [
        run for run in runs
        if run.get("head_sha") == sha and run.get("event") == "push"
    ]
    latest = max(relevant, key=lambda run: run.get("id", 0), default=None)
    if latest is None or latest.get("status") != "completed":
        return "pending"
    return "success" if latest.get("conclusion") == "success" else "failure"


def successful_run(runs: Iterable[dict[str, Any]], sha: str) -> bool:
    return workflow_gate_state(runs, sha) == "success"


def _github_json(repository: str, path: str, parameters: dict[str, str] | None = None) -> Any:
    query = "?" + urllib.parse.urlencode(parameters) if parameters else ""
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/{path}{query}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "tag-release-automation",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def check_workflows(repository: str, sha: str, wait_seconds: int) -> list[str]:
    deadline = time.monotonic() + wait_seconds
    pending = list(REQUIRED_WORKFLOWS)
    while True:
        pending = []
        for workflow in REQUIRED_WORKFLOWS:
            payload = _github_json(repository, f"actions/workflows/{workflow}/runs", {
                "head_sha": sha, "event": "push", "per_page": "20"
            })
            runs = payload.get("workflow_runs", [])
            state = workflow_gate_state(runs, sha)
            if state != "success":
                if state == "failure":
                    return [f"{workflow} did not pass for {sha}"]
                pending.append(workflow)
        if not pending:
            return []
        if time.monotonic() >= deadline:
            return [f"no successful push run for {workflow} at {sha}" for workflow in pending]
        time.sleep(min(15, max(0, deadline - time.monotonic())))


def find_edge_artifact(repository: str, sha: str) -> tuple[int, str] | None:
    name = f"tag-edge-{sha}"
    payload = _github_json(repository, "actions/artifacts", {"name": name, "per_page": "100"})
    candidates = [
        artifact for artifact in payload.get("artifacts", [])
        if not artifact.get("expired")
        and artifact.get("name") == name
        and artifact.get("workflow_run", {}).get("id") is not None
    ]
    if not candidates:
        return None
    artifact = max(candidates, key=lambda item: item.get("created_at", ""))
    return int(artifact["workflow_run"]["id"]), name


def find_edge_runs(repository: str, sha: str) -> list[dict[str, Any]]:
    """Return edge build runs that processed exactly this main commit."""
    matches = []
    page = 1
    while True:
        payload = _github_json(repository, f"actions/workflows/{EDGE_WORKFLOW}/runs", {
            "event": "workflow_run", "per_page": "100", "page": str(page)
        })
        runs = payload.get("workflow_runs", [])
        matches.extend(
            run for run in runs
            if run.get("display_title") == f"Edge build {sha}"
            # Runs created before run-name was set are titled only "Edge build".
            or (run.get("display_title") == "Edge build" and run.get("head_sha") == sha)
        )
        # Inspect every page: another matching run may still be active.
        if len(runs) < 100:
            return matches
        page += 1


def wait_for_predecessor(repository: str, sha: str, wait_seconds: int) -> list[str]:
    """Wait until the preceding main commit is ineligible or fully processed.

    A predecessor whose release processing finished unsuccessfully does not
    block later commits; otherwise one failure would fail every later merge.
    """
    deadline = time.monotonic() + wait_seconds
    while True:
        gate_states = []
        for workflow in REQUIRED_WORKFLOWS:
            payload = _github_json(repository, f"actions/workflows/{workflow}/runs", {
                "head_sha": sha, "event": "push", "per_page": "20"
            })
            runs = payload.get("workflow_runs", [])
            if any(run.get("head_sha") == sha and run.get("event") == "push" for run in runs):
                gate_states.append(workflow_gate_state(runs, sha))
            else:
                gate_states.append("missing")

        if "failure" in gate_states:
            return []
        if "pending" not in gate_states and "missing" in gate_states:
            # Commits pushed together, marked [skip ci], or pushed while
            # automation was disabled never ran the required gates.
            print(f"predecessor {sha} never ran the required workflows; continuing")
            return []

        if gate_states and all(state == "success" for state in gate_states):
            artifact = find_edge_artifact(repository, sha)
            runs = find_edge_runs(repository, sha)
            if artifact is not None:
                runs.append(_github_json(repository, f"actions/runs/{artifact[0]}"))
            if runs and all(run.get("status") == "completed" for run in runs):
                if not any(run.get("conclusion") == "success" for run in runs):
                    print(f"edge build did not pass for predecessor {sha}; continuing")
                return []

        if time.monotonic() >= deadline:
            return [f"predecessor {sha} has not completed release processing"]
        time.sleep(min(15, max(0, deadline - time.monotonic())))


def _load_provenance(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return None, [f"invalid BUILD-PROVENANCE.json: {error}"]
    if not isinstance(value, dict):
        return None, ["invalid BUILD-PROVENANCE.json: expected a JSON object"]
    return value, []


def _validate_provenance(
    provenance: dict[str, Any], *, channel: str, archive_name: str,
    digest: str, version: str, sha: str | None,
) -> list[str]:
    errors: list[str] = []
    expected: dict[str, Any] = {
        "schema_version": 1,
        "channel": channel,
        "version": version,
        "archive": {"name": archive_name, "sha256": digest},
    }
    if sha is not None:
        expected["commit_sha"] = sha
    for key, value in expected.items():
        if provenance.get(key) != value:
            errors.append(f"invalid provenance {key}: expected {value!r}")
    # Installed Tag versions accept only source_ref refs/heads/main, so a
    # maintenance build keeps it and names its branch in source_branch. v0.3.1
    # was published with the branch in source_ref, so that is still accepted.
    source_ref = provenance.get("source_ref")
    if not isinstance(source_ref, str):
        errors.append("invalid provenance source_ref: expected a branch reference")
    else:
        errors.extend(validate_source_ref(source_ref, version))
    if "source_branch" in provenance:
        source_branch = provenance["source_branch"]
        if not isinstance(source_branch, str) or source_branch == "main":
            errors.append("invalid provenance source_branch: expected a release/vX.Y.x branch")
        else:
            errors.extend(
                error.replace("source_ref", "source_branch")
                for error in validate_source_ref(f"refs/heads/{source_branch}", version)
            )
    built_at = provenance.get("built_at")
    try:
        parsed = datetime.fromisoformat(built_at.removesuffix("Z") + "+00:00")
        valid_built_at = built_at.endswith("Z") and parsed.utcoffset() == timezone.utc.utcoffset(None)
    except (AttributeError, TypeError, ValueError):
        valid_built_at = False
    if not valid_built_at:
        errors.append("invalid provenance built_at: expected an ISO-8601 UTC timestamp")
    return errors


def _archive_version(archive: Path, label: str) -> tuple[str | None, list[str]]:
    try:
        with zipfile.ZipFile(archive) as bundle:
            return bundle.read("VERSION").decode("utf-8").strip(), []
    except KeyError:
        return None, [f"{label} archive does not contain VERSION"]
    except (OSError, UnicodeError, zipfile.BadZipFile) as error:
        return None, [f"invalid {label} archive: {error}"]


def validate_edge_bundle(directory: Path, sha: str, version: str) -> list[str]:
    errors: list[str] = []
    archive = directory / "tag-edge.zip"
    provenance_path = directory / "BUILD-PROVENANCE.json"
    checksum_path = directory / "SHA256SUMS"
    for path in (archive, provenance_path, checksum_path):
        if not path.is_file():
            errors.append(f"missing edge artifact: {path.name}")
    if errors:
        return errors
    provenance, provenance_errors = _load_provenance(provenance_path)
    errors.extend(provenance_errors)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if provenance is not None:
        errors.extend(_validate_provenance(
            provenance, channel="edge", archive_name=archive.name,
            digest=digest, version=version, sha=sha,
        ))
    try:
        checksum = checksum_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        errors.append(f"invalid SHA256SUMS: {error}")
        checksum = None
    if checksum is not None and checksum != f"{digest}  {archive.name}\n":
        errors.append("SHA256SUMS does not match the edge archive")
    archived_version, archive_errors = _archive_version(archive, "edge")
    errors.extend(archive_errors)
    if archived_version is not None and archived_version != version:
        errors.append(f"edge archive VERSION is {archived_version!r}, expected {version!r}")
    return errors


def promote_edge_bundle(directory: Path, output: Path, version: str) -> Path:
    edge_provenance, errors = _load_provenance(directory / "BUILD-PROVENANCE.json")
    if edge_provenance is None:
        raise ValueError("; ".join(errors))
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"tag-{version}.zip"
    shutil.copy2(directory / "tag-edge.zip", destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    (output / "SHA256SUMS").write_text(f"{digest}  {destination.name}\n", encoding="utf-8")
    release_provenance = {
        **edge_provenance,
        "channel": "release",
        "version": version,
        "archive": {"name": destination.name, "sha256": digest},
    }
    (output / "BUILD-PROVENANCE.json").write_text(
        json.dumps(release_provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return destination


def validate_release_bundle(
    directory: Path, version: str, sha: str | None = None, require_provenance: bool = True
) -> list[str]:
    archive = directory / f"tag-{version}.zip"
    checksum_path = directory / "SHA256SUMS"
    provenance_path = directory / "BUILD-PROVENANCE.json"
    required = [archive, checksum_path]
    if require_provenance:
        required.append(provenance_path)
    errors = [f"missing release asset: {path.name}" for path in required if not path.is_file()]
    if errors:
        return errors
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    try:
        checksum = checksum_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        errors.append(f"invalid SHA256SUMS: {error}")
        checksum = None
    if checksum is not None and checksum != f"{digest}  {archive.name}\n":
        errors.append("SHA256SUMS does not match the release archive")
    archived_version, archive_errors = _archive_version(archive, "release")
    errors.extend(archive_errors)
    if archived_version is not None and archived_version != version:
        errors.append(f"release archive VERSION is {archived_version!r}, expected {version!r}")
    if require_provenance:
        provenance, provenance_errors = _load_provenance(provenance_path)
        errors.extend(provenance_errors)
        if provenance is not None:
            errors.extend(_validate_provenance(
                provenance, channel="release", archive_name=archive.name,
                digest=digest, version=version, sha=sha,
            ))
    return errors


def _print_errors(errors: list[str]) -> int:
    for error in errors:
        print(f"- {error}", file=sys.stderr)
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    candidate = subparsers.add_parser("validate-candidate")
    candidate.add_argument("--version", required=True)
    candidate.add_argument("--phase", choices=("alpha", "beta", "stable"), required=True)
    candidate.add_argument("--allow-existing-version", action="store_true")
    candidate.add_argument("--line", help="MAJOR.MINOR line of a maintenance branch")

    branch = subparsers.add_parser("validate-branch")
    branch.add_argument("--name", required=True)
    branch.add_argument("--print-line", action="store_true")

    automatic = subparsers.add_parser("next-prerelease")
    automatic.add_argument("--base-version", required=True)
    automatic.add_argument("--label", action="append", default=[])

    release_tag = subparsers.add_parser("validate-release-tag")
    release_tag.add_argument("--source-version", required=True)
    release_tag.add_argument("--tag", required=True)

    commit = subparsers.add_parser("validate-commit")
    commit.add_argument("--sha", required=True)
    commit.add_argument("--main-ref", default="origin/main")

    workflows = subparsers.add_parser("check-workflows")
    workflows.add_argument("--repository", required=True)
    workflows.add_argument("--sha", required=True)
    workflows.add_argument("--wait-seconds", type=int, default=0)

    predecessor = subparsers.add_parser("wait-for-predecessor")
    predecessor.add_argument("--repository", required=True)
    predecessor.add_argument("--sha", required=True)
    predecessor.add_argument("--wait-seconds", type=int, default=0)

    artifact = subparsers.add_parser("find-edge-artifact")
    artifact.add_argument("--repository", required=True)
    artifact.add_argument("--sha", required=True)

    bundle = subparsers.add_parser("validate-edge")
    bundle.add_argument("--directory", type=Path, required=True)
    bundle.add_argument("--sha", required=True)
    bundle.add_argument("--version", required=True)

    promote = subparsers.add_parser("promote-edge")
    promote.add_argument("--directory", type=Path, required=True)
    promote.add_argument("--output", type=Path, required=True)
    promote.add_argument("--version", required=True)

    release = subparsers.add_parser("validate-release")
    release.add_argument("--directory", type=Path, required=True)
    release.add_argument("--version", required=True)
    release.add_argument("--sha")
    release.add_argument("--allow-missing-provenance", action="store_true")

    channel_index = subparsers.add_parser("write-channel-index")
    channel_index.add_argument("--repository", required=True)
    channel_index.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "validate-candidate":
        tags = subprocess.check_output(["git", "tag", "--list", "v*"], text=True).splitlines()
        if args.line:
            return _print_errors(validate_maintenance_candidate(
                args.version, args.line, tags, args.allow_existing_version
            ))
        return _print_errors(validate_candidate(
            args.version, args.phase, tags, args.allow_existing_version
        ))
    if args.command == "validate-branch":
        if args.name == "main":
            return 0
        match = MAINTENANCE_BRANCH_RE.fullmatch(args.name)
        if not match:
            return _print_errors([f"{args.name!r} is neither main nor a release/vX.Y.x branch"])
        if args.print_line:
            print(f"{match[1]}.{match[2]}")
        return 0
    if args.command == "next-prerelease":
        tags = subprocess.check_output(["git", "tag", "--list", "v*"], text=True).splitlines()
        try:
            version = select_auto_prerelease(args.base_version, tags, args.label)
        except ValueError as error:
            return _print_errors([str(error)])
        print(version if version is not None else "skip")
        return 0
    if args.command == "validate-release-tag":
        return _print_errors(validate_release_tag(args.source_version, args.tag))
    if args.command == "validate-commit":
        if not SHA_RE.fullmatch(args.sha):
            return _print_errors([
                "selected commit must be a full, lowercase 40-character SHA"
            ])
        resolved = subprocess.run(
            ["git", "rev-parse", "--verify", f"{args.sha}^{{commit}}"],
            capture_output=True, text=True,
        ).stdout.strip()
        on_main = subprocess.run(
            ["git", "merge-base", "--is-ancestor", args.sha, args.main_ref]
        ).returncode == 0
        return _print_errors(validate_selected_sha(args.sha, resolved, on_main, args.main_ref))
    if args.command == "check-workflows":
        return _print_errors(check_workflows(args.repository, args.sha, args.wait_seconds))
    if args.command == "wait-for-predecessor":
        return _print_errors(wait_for_predecessor(
            args.repository, args.sha, args.wait_seconds
        ))
    if args.command == "find-edge-artifact":
        found = find_edge_artifact(args.repository, args.sha)
        if not found:
            return _print_errors([f"no unexpired edge artifact found for {args.sha}"])
        print(f"{found[0]}\t{found[1]}")
        return 0
    if args.command == "validate-edge":
        return _print_errors(validate_edge_bundle(args.directory, args.sha, args.version))
    if args.command == "promote-edge":
        print(promote_edge_bundle(args.directory, args.output, args.version))
        return 0
    if args.command == "validate-release":
        return _print_errors(validate_release_bundle(
            args.directory, args.version, args.sha, not args.allow_missing_provenance
        ))
    if args.command == "write-channel-index":
        write_channel_index(args.repository, args.output)
        print(args.output)
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
