#!/usr/bin/env python3
# Modified by klovr.co in 2026 for Tag. See NOTICE and repository history.
from __future__ import annotations

import argparse
import base64
import configparser
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


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
    status = "ok" if ok else "fail"
    suffix = f" - {detail}" if detail else ""
    print(f"[{status}] {label}{suffix}")


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


def selected_transport() -> str:
    transport = env("OPENTAG_TRANSPORT") or "slack"
    if transport in {"slack", "zulip", "both"}:
        return transport
    print_check(False, "OPENTAG_TRANSPORT", "must be slack, zulip, or both")
    return ""


def selected_zulip_engine(transport: str) -> str:
    if transport not in {"zulip", "both"}:
        return "native"
    engine = env("OPENTAG_ZULIP_ENGINE") or "native"
    ok = engine in {"native", "zulipmcp"}
    print_check(ok, "OPENTAG_ZULIP_ENGINE", engine if ok else "must be native or zulipmcp")
    return engine if ok else ""


def check_env(transport: str, zulip_engine: str) -> bool:
    required = [
        "MFS_URL",
        "MFS_ALLOWED_SCOPES",
        "OPENTAG_BACKEND",
    ]
    if transport in {"slack", "both"}:
        required.extend(["SLACK_APP_TOKEN", "SLACK_BOT_TOKEN"])
    if transport in {"zulip", "both"}:
        required.append("ZULIP_CONFIG_FILE")
        if zulip_engine == "zulipmcp":
            required.append("OPENTAG_WORKDIR")
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


def read_zuliprc(path_value: str) -> tuple[bool, dict[str, str], str]:
    path = Path(path_value).expanduser()
    if not path.is_file():
        return False, {}, f"file not found: {path}"

    parser = configparser.ConfigParser()
    try:
        parser.read(path)
        config = parser["api"]
    except (configparser.Error, KeyError) as exc:
        return False, {}, f"invalid zuliprc: {exc}"

    values = {name: config.get(name, "").strip() for name in ("site", "email", "key")}
    missing = [name for name, value in values.items() if not value]
    if missing:
        return False, {}, f"missing {', '.join(missing)} in [api]"
    return True, values, "loaded"


def check_zulip_config(path_value: str, label: str) -> bool:
    ok, values, detail = read_zuliprc(path_value)
    print_check(ok, f"{label} config", detail)
    if not ok:
        return False

    credentials = f"{values['email']}:{values['key']}".encode()
    authorization = base64.b64encode(credentials).decode()
    url = f"{values['site'].rstrip('/')}/api/v1/users/me"
    ok, data = request_json(url, headers={"Authorization": f"Basic {authorization}"})
    identity = data.get("full_name") or data.get("email") or data.get("msg") or data.get("error")
    print_check(ok and data.get("result") == "success", f"{label} auth", str(identity))
    return ok and data.get("result") == "success"


def check_zulipmcp_runtime() -> bool:
    runtime = Path(__file__).with_name("zulip_runtime.py")
    result = subprocess.run(
        [sys.executable, str(runtime), "--print-command"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
    )
    command_ok = result.returncode == 0
    detail = "launch policy validated" if command_ok else result.stdout.strip()[-500:]
    print_check(command_ok, "ZulipMCP OpenTag runtime", detail)
    if not command_ok:
        return False

    from zulip_runtime import ZULIPMCP_PACKAGE

    uv_path = shutil.which("uv")
    if not uv_path:
        print_check(False, "ZulipMCP dependency", "uv executable missing")
        return False
    result = subprocess.run(
        [
            uv_path,
            "run",
            "--with",
            ZULIPMCP_PACKAGE,
            "python3",
            "-c",
            "import zulipmcp; print('imported')",
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
    )
    dependency_ok = result.returncode == 0 and "imported" in result.stdout
    print_check(
        dependency_ok,
        "ZulipMCP pinned dependency",
        "imported" if dependency_ok else result.stdout.strip()[-500:],
    )

    private_streams = env("BOT_ALLOWED_PRIVATE_STREAMS")
    print_check(
        True,
        "ZulipMCP private-stream policy",
        "explicit allowlist set" if private_streams else "public streams only",
    )
    return dependency_ok


def check_zulip(zulip_engine: str) -> bool:
    all_ok = check_zulip_config(env("ZULIP_CONFIG_FILE"), "Zulip bot")
    auto_grant = env("ZULIP_AUTO_GRANT_PRIVATE_HISTORY").lower() == "true"
    if zulip_engine == "zulipmcp":
        auto_grant_ok = not auto_grant
        print_check(
            auto_grant_ok,
            "ZULIP_AUTO_GRANT_PRIVATE_HISTORY",
            "disabled" if auto_grant_ok else "must be false with zulipmcp",
        )
        return check_zulipmcp_runtime() and auto_grant_ok and all_ok
    if auto_grant:
        admin_path = env("ZULIP_ADMIN_CONFIG_FILE")
        if not admin_path:
            print_check(False, "ZULIP_ADMIN_CONFIG_FILE", "required when auto-grant is enabled")
            return False
        all_ok = check_zulip_config(admin_path, "Zulip admin") and all_ok
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
    transport = selected_transport()
    backend = env("OPENTAG_BACKEND")
    workspace = Path(env("OPENTAG_WORKDIR")).expanduser()
    scopes = [scope.strip() for scope in env("MFS_ALLOWED_SCOPES").split(",") if scope.strip()]
    checks = {
        "supported transport": transport in {"slack", "zulip", "both"},
        "supported backend": backend in {"codex", "claude"},
        "agent workspace": workspace.is_dir(),
        "MFS URL": env("MFS_URL").startswith(("http://", "https://")),
        "MFS allowed scopes": bool(scopes),
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
    return all(checks.values()) and metadata_ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight a Tag chat + MFS setup.")
    parser.add_argument("--channel-id", help="Optional Slack channel ID to verify bot access.")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="check configuration shape and local release files without credentials or network",
    )
    args = parser.parse_args()

    if args.offline:
        return 0 if check_offline(Path(__file__).resolve().parents[1]) else 1

    transport = selected_transport()
    if not transport:
        return 1
    zulip_engine = selected_zulip_engine(transport)
    if transport in {"zulip", "both"} and not zulip_engine:
        return 1
    scopes = [scope.strip() for scope in env("MFS_ALLOWED_SCOPES").split(",") if scope.strip()]
    checks = [
        check_env(transport, zulip_engine),
        check_mfs(scopes) if scopes else False,
        check_backend(),
    ]
    if transport in {"slack", "both"}:
        checks.append(check_slack(args.channel_id))
    if transport in {"zulip", "both"}:
        checks.append(check_zulip(zulip_engine))
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
