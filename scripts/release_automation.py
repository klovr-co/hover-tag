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
REQUIRED_WORKFLOWS = ("ci.yml", "install-smoke.yml")


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


def validate_selected_sha(sha: str, resolved: str, is_on_main: bool) -> list[str]:
    errors: list[str] = []
    if not SHA_RE.fullmatch(sha):
        errors.append("selected commit must be a full, lowercase 40-character SHA")
    if resolved != sha:
        errors.append("selected commit did not resolve to the requested SHA")
    if not is_on_main:
        errors.append("selected commit is not reachable from origin/main")
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
        and artifact.get("workflow_run", {}).get("head_sha") == sha
    ]
    if not candidates:
        return None
    artifact = max(candidates, key=lambda item: item.get("created_at", ""))
    return int(artifact["workflow_run"]["id"]), name


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
        "source_ref": "refs/heads/main",
        "version": version,
        "archive": {"name": archive_name, "sha256": digest},
    }
    if sha is not None:
        expected["commit_sha"] = sha
    for key, value in expected.items():
        if provenance.get(key) != value:
            errors.append(f"invalid provenance {key}: expected {value!r}")
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

    commit = subparsers.add_parser("validate-commit")
    commit.add_argument("--sha", required=True)
    commit.add_argument("--main-ref", default="origin/main")

    workflows = subparsers.add_parser("check-workflows")
    workflows.add_argument("--repository", required=True)
    workflows.add_argument("--sha", required=True)
    workflows.add_argument("--wait-seconds", type=int, default=0)

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

    args = parser.parse_args()
    if args.command == "validate-candidate":
        tags = subprocess.check_output(["git", "tag", "--list", "v*"], text=True).splitlines()
        return _print_errors(validate_candidate(
            args.version, args.phase, tags, args.allow_existing_version
        ))
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
        return _print_errors(validate_selected_sha(args.sha, resolved, on_main))
    if args.command == "check-workflows":
        return _print_errors(check_workflows(args.repository, args.sha, args.wait_seconds))
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
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
