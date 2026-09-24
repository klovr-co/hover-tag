#!/usr/bin/env python3
# Modified by klovr.co in 2026 for Tag. See NOTICE and repository history.
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CHECK_RESULTS: list[dict[str, Any]] | None = None
RUNTIME_DEPENDENCIES = ("mfs_server", "psutil", "slack_bolt")


def recovery_hint(label: str) -> str:
    if label == "Slack search users:read permission":
        return "Add users:read under OAuth & Permissions > Bot Token Scopes, reinstall the Slack app, and update Tag's bot token if Slack replaces it"
    if label == "Slack search user lookup":
        return "Check the Slack bot token and users.info access; Slack search must verify caller identity"
    if label == "Tag runtime dependencies":
        return "Re-run the Tag installer; for a source checkout, run ./install.sh --dependencies-only"
    if label.startswith("MFS"):
        return "Check tag config show, start MFS, and index the configured sources before retrying"
    if label.startswith("Slack") or label.startswith("SLACK_"):
        return "Check Slack tokens, allowed member IDs, channel membership, and app scopes in tag setup or tag config"
    if label.startswith("backend") or label == "OPENTAG_BACKEND":
        return "Install and sign in with the selected CLI; change it with tag config set OPENTAG_BACKEND codex|claude"
    return "Review tag inspect --json and tag config show; rerun the installer for missing runtime files"


def env(name: str) -> str:
    return os.getenv(name, "").strip()


def token_from_env() -> str | None:
    if env("MFS_TOKEN"):
        return env("MFS_TOKEN")
    token_file = Path.home() / ".mfs" / "server.token"
    if token_file.exists():
        return token_file.read_text().strip()
    return None


def print_check(ok: bool, label: str, detail: str = "") -> None:
    if CHECK_RESULTS is not None:
        # Remote error bodies and credential values never enter machine output.
        CHECK_RESULTS.append({"check": label, "ok": bool(ok),
                              "next_action": None if ok else recovery_hint(label)})
    status = "ok" if ok else "fail"
    suffix = f" - {detail}" if detail else ""
    print(f"[{status}] {label}{suffix}")


def check_runtime_dependencies() -> bool:
    missing = [name for name in RUNTIME_DEPENDENCIES if importlib.util.find_spec(name) is None]
    print_check(
        not missing,
        "Tag runtime dependencies",
        "available" if not missing else "missing: " + ", ".join(missing),
    )
    return not missing


def request_json(
    url: str,
    *,
    token: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 20,
) -> tuple[bool, dict[str, Any]]:
    headers = dict(headers or {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return True, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            body = {"error": str(exc)}
        return False, body
    except Exception as exc:  # noqa: BLE001
        return False, {"error": f"{type(exc).__name__}: {exc}"}


def slack_api(
    method: str, token: str, params: dict[str, str] | None = None
) -> tuple[bool, dict[str, Any]]:
    query = f"?{urllib.parse.urlencode(params or {})}" if params else ""
    ok, data = request_json(f"https://slack.com/api/{method}{query}", token=token)
    return ok and bool(data.get("ok")), data


def check_env() -> bool:
    required = [
        "MFS_URL",
        "MFS_ALLOWED_SCOPES",
        "OPENTAG_BACKEND",
    ]
    required.extend(["SLACK_APP_TOKEN", "SLACK_BOT_TOKEN", "SLACK_ALLOWED_USER_IDS"])
    all_ok = True
    for name in required:
        value = env(name)
        ok = bool(value)
        all_ok = all_ok and ok
        detail = "set" if ok else "missing"
        if name == "SLACK_APP_TOKEN" and value:
            detail = (
                "set, expected xapp-* token"
                if value.startswith("xapp-")
                else "set, unexpected prefix"
            )
            ok = value.startswith("xapp-")
        if name == "SLACK_BOT_TOKEN" and value:
            detail = (
                "set, expected xoxb-* token"
                if value.startswith("xoxb-")
                else "set, unexpected prefix"
            )
            ok = value.startswith("xoxb-")
        if name == "SLACK_ALLOWED_USER_IDS":
            user_ids = [user_id.strip() for user_id in value.split(",") if user_id.strip()]
            ok = bool(user_ids)
            detail = f"{len(set(user_ids))} user(s)" if ok else "missing"
        print_check(ok, name, detail)
        all_ok = all_ok and ok

    mfs_token = token_from_env()
    print_check(
        bool(mfs_token),
        "MFS_TOKEN or ~/.mfs/server.token",
        "available" if mfs_token else "missing",
    )
    return all_ok and bool(mfs_token)


def check_mfs(scopes: list[str]) -> bool:
    base = env("MFS_URL").rstrip("/") or "http://127.0.0.1:13619"
    token = token_from_env()
    ok, data = request_json(f"{base}/healthz")
    print_check(ok, "MFS healthz", data.get("status") or data.get("error", "reachable"))
    if not ok:
        print(f"       hint: MFS server is not reachable at {base}.")
        print("       Install and start it first: uv tool install mfs-server && mfs-server run")
    all_ok = ok

    ok, data = request_json(f"{base}/v1/status", token=token)
    connector_count = len(data.get("connectors") or []) if isinstance(data, dict) else 0
    print_check(
        ok,
        "MFS /v1/status",
        f"{connector_count} connectors" if ok else str(data.get("error")),
    )
    if ok and connector_count == 0:
        print("       hint: no sources indexed yet. Add one with the mfs-ingest skill.")
    all_ok = all_ok and ok

    for scope in scopes:
        params = urllib.parse.urlencode({"path": scope})
        ok, data = request_json(f"{base}/v1/ls?{params}", token=token)
        detail = "listed" if ok else str(data.get("error") or data.get("detail"))
        print_check(ok, f"MFS scope {scope}", detail)
        all_ok = all_ok and ok
    return all_ok


def check_slack(channel_id: str | None) -> bool:
    bot_token = env("SLACK_BOT_TOKEN")
    all_ok = True
    ok, data = slack_api("auth.test", bot_token)
    detail = data.get("team") or data.get("error") or "authenticated"
    print_check(ok, "Slack bot auth.test", detail)
    all_ok = all_ok and ok

    if ok:
        # auth.test needs no scopes; probe the lookup used by search grants
        # against the authenticated bot user, without depending on a caller.
        user_id = data.get("user_id")
        user_ok, user_data = (
            slack_api("users.info", bot_token, {"user": user_id})
            if user_id else (False, {"error": "auth.test returned no user ID"})
        )
        label = (
            "Slack search users:read permission"
            if user_data.get("error") == "missing_scope"
            else "Slack search user lookup"
        )
        print_check(user_ok, label, "available" if user_ok else recovery_hint(label))
        all_ok = all_ok and user_ok

    if channel_id:
        ok, data = slack_api("conversations.info", bot_token, {"channel": channel_id})
        if ok:
            channel = data.get("channel") or {}
            detail = f"name={channel.get('name')}, member={channel.get('is_member')}, private={channel.get('is_private')}"
        else:
            detail = data.get("error") or "failed"
        print_check(ok, f"Slack channel {channel_id}", detail)
        all_ok = all_ok and ok

        ok, data = slack_api(
            "conversations.history", bot_token, {"channel": channel_id, "limit": "1"}
        )
        detail = "history readable" if ok else data.get("error") or "failed"
        print_check(ok, f"Slack channel history {channel_id}", detail)
        all_ok = all_ok and ok

    return all_ok


def check_backend() -> bool:
    backend = env("OPENTAG_BACKEND")
    if backend == "claude":
        ok = shutil.which("claude") is not None
        print_check(
            ok,
            "backend claude",
            "claude executable found" if ok else "claude executable missing",
        )
        return ok
    if backend == "codex":
        ok = shutil.which("codex") is not None
        print_check(
            ok,
            "backend codex",
            "codex executable found" if ok else "codex executable missing",
        )
        return ok
    print_check(False, "OPENTAG_BACKEND", "must be claude or codex")
    return False


def check_offline(root: Path) -> bool:
    """Validate a launch configuration without credentials or network access."""
    backend = env("OPENTAG_BACKEND")
    workspace = Path(env("OPENTAG_WORKDIR")).expanduser()
    scopes = [scope.strip() for scope in env("MFS_ALLOWED_SCOPES").split(",") if scope.strip()]
    runtime_ok = check_runtime_dependencies()
    checks = {
        "supported transport": (env("OPENTAG_TRANSPORT") or "slack") == "slack",
        "supported backend": backend in {"codex", "claude"},
        "agent workspace": workspace.is_dir(),
        "MFS URL": env("MFS_URL").startswith(("http://", "https://")),
        "MFS allowed scopes": bool(scopes),
        "Slack allowed users": bool(
            [value for value in env("SLACK_ALLOWED_USER_IDS").split(",") if value.strip()]
        ),
        "Slack app manifest": (root / "slack-app-manifest.yaml").is_file(),
        "Tag command": (root / "tag").is_file() and os.access(root / "tag", os.X_OK),
        "installer": (root / "install.sh").is_file()
        and os.access(root / "install.sh", os.X_OK),
    }
    for label, ok in checks.items():
        print_check(ok, label)

    try:
        try:
            from release_check import validate_release
        except ImportError:  # Imported as scripts.opentag_doctor by tests.
            from scripts.release_check import validate_release
        metadata_errors = validate_release(root)
    except (ImportError, OSError) as exc:
        metadata_errors = [str(exc)]
    metadata_ok = not metadata_errors
    print_check(metadata_ok, "release metadata")
    for error in metadata_errors:
        print(f"       {error}")
    return runtime_ok and all(checks.values()) and metadata_ok


def run_checks(offline: bool, channel_ids: list[str] | None) -> int:
    if offline:
        return 0 if check_offline(Path(__file__).resolve().parents[1]) else 1
    if (env("OPENTAG_TRANSPORT") or "slack") != "slack":
        print_check(False, "OPENTAG_TRANSPORT", "must be slack")
        return 1
    scopes = [scope.strip() for scope in env("MFS_ALLOWED_SCOPES").split(",") if scope.strip()]
    checks = [
        check_runtime_dependencies(),
        check_env(),
        check_mfs(scopes) if scopes else False,
        check_backend(),
    ]
    configured = channel_ids or []
    checks.extend(check_slack(channel_id) for channel_id in configured)
    if not configured:
        checks.append(check_slack(None))
    return 0 if all(checks) else 1


def main() -> int:
    global CHECK_RESULTS
    parser = argparse.ArgumentParser(description="Preflight a Tag chat + MFS setup.")
    parser.add_argument("--channel-id", action="append", dest="channel_ids",
                        help="Slack channel ID to verify; repeat for multiple channels.")
    parser.add_argument("--json", action="store_true", help="emit structured checks without raw service responses")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="check configuration shape and local release files without credentials or network",
    )
    args = parser.parse_args()

    if not args.json:
        return run_checks(args.offline, args.channel_ids)
    CHECK_RESULTS = []
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            result = run_checks(args.offline, args.channel_ids)
        print(json.dumps({"schema_version": 1, "ok": result == 0, "offline": args.offline,
                          "checks": CHECK_RESULTS, "backend_authentication": "not_checked",
                          "backend_task_execution": "not_checked", "first_reply": "not_verified"}, indent=2))
        return result
    finally:
        CHECK_RESULTS = None


if __name__ == "__main__":
    raise SystemExit(main())
