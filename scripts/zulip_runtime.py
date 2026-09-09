#!/usr/bin/env python3
"""Select and launch OpenTag's configured Zulip runtime adapter."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
from collections.abc import Mapping
from pathlib import Path

try:
    from opentag_process_env import isolated_environment
except ModuleNotFoundError:  # Imported as scripts.zulip_runtime by tests.
    from scripts.opentag_process_env import isolated_environment


ROOT = Path(__file__).resolve().parents[1]


def runtime_requirement(package: str) -> str:
    prefix = f"{package}=="
    requirements = ROOT / "requirements-runtime.txt"
    for raw_line in requirements.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith(prefix):
            return line
    raise RuntimeError(f"{requirements} does not pin {package}")


ZULIPMCP_COMMIT = "2ac06bd12b4a10c4b41ecc31392c96285e3d8f05"
ZULIPMCP_PACKAGE = (
    "zulipmcp @ git+https://github.com/zulip/zulipmcp.git@" + ZULIPMCP_COMMIT
)
ZULIP_PACKAGE = runtime_requirement("zulip")
VALID_ENGINES = {"native", "zulipmcp"}
VALID_BACKENDS = {"claude", "codex"}
VALID_CODEX_PERMISSION_MODES = {"parity", "workspace-write", "read-only", "none"}


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _positive_int(value: str, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _positive_float(value: str, name: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be greater than zero") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return parsed


def _number_text(value: float) -> str:
    return f"{value:g}"


def selected_engine(env: Mapping[str, str]) -> str:
    engine = env.get("OPENTAG_ZULIP_ENGINE", "native").strip().lower() or "native"
    if engine not in VALID_ENGINES:
        raise ValueError("OPENTAG_ZULIP_ENGINE must be native or zulipmcp")
    return engine


def sanitized_environment(source: Mapping[str, str], *, engine: str) -> dict[str, str]:
    """Remove credentials that the selected Zulip adapter never needs."""
    if engine not in VALID_ENGINES:
        raise ValueError("OPENTAG_ZULIP_ENGINE must be native or zulipmcp")
    clean = isolated_environment(source, transport="zulip")

    if engine == "zulipmcp":
        # The upstream listener does not implement OpenTag's admin auto-grant
        # flow. Keeping the admin credential out of persistent agent processes
        # also prevents an accidental privilege expansion.
        clean.pop("ZULIP_ADMIN_CONFIG_FILE", None)
        clean["ZULIP_AUTO_GRANT_PRIVATE_HISTORY"] = "false"

    return clean


def render_mcp_config() -> str:
    config = {
        "mcpServers": {
            "zulip": {
                "command": "uv",
                "args": [
                    "run",
                    "--with",
                    ZULIPMCP_PACKAGE,
                    "python3",
                    "-m",
                    "zulipmcp.mcp",
                ],
                "env": {
                    "ZULIP_RC_PATH": "${ZULIP_RC_PATH}",
                    "BOT_ALLOWED_PRIVATE_STREAMS": "${BOT_ALLOWED_PRIVATE_STREAMS:-}",
                    "BOT_ALLOWED_WRITE_STREAMS": "${BOT_ALLOWED_WRITE_STREAMS:-}",
                },
            }
        }
    }
    return json.dumps(config, indent=2) + "\n"


def render_system_prompt(*, root: Path, idle_hours: float) -> str:
    template_path = root / "references" / "zulipmcp-runtime.md"
    template = template_path.read_text(encoding="utf-8")
    replacements = {
        "{{IDLE_HOURS}}": _number_text(idle_hours),
        "{{SKILL_DIR}}": str(root),
    }
    rendered = template
    for marker, value in replacements.items():
        rendered = rendered.replace(marker, value)
    if "{{" in rendered or "}}" in rendered:
        raise ValueError(f"unresolved marker in {template_path}")
    return rendered


def prepare_runtime_files(
    *, root: Path, runtime_dir: Path, idle_hours: float
) -> tuple[Path, Path]:
    runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    runtime_dir.chmod(0o700)
    mcp_path = runtime_dir / "zulipmcp.mcp.json"
    prompt_path = runtime_dir / "zulipmcp-system-prompt.md"
    mcp_path.write_text(render_mcp_config(), encoding="utf-8")
    prompt_path.write_text(
        render_system_prompt(root=root, idle_hours=idle_hours), encoding="utf-8"
    )
    mcp_path.chmod(0o600)
    prompt_path.chmod(0o600)
    return mcp_path, prompt_path


def build_runtime_command(
    env: Mapping[str, str],
    *,
    root: Path = ROOT,
    runtime_dir: Path | None = None,
) -> list[str]:
    engine = selected_engine(env)
    backend = _required(env, "OPENTAG_BACKEND").lower()
    if backend not in VALID_BACKENDS:
        raise ValueError("OPENTAG_BACKEND must be claude or codex")

    if engine == "native":
        return [
            "uv",
            "run",
            "--with",
            ZULIP_PACKAGE,
            "python3",
            str(root / "scripts" / "zulip_agent.py"),
            "--backend",
            backend,
        ]

    config_file = _required(env, "ZULIP_CONFIG_FILE")
    workdir = _required(env, "OPENTAG_WORKDIR")
    permission_mode = env.get(
        "OPENTAG_ZULIPMCP_CODEX_PERMISSION_MODE", "workspace-write"
    ).strip() or "workspace-write"
    if permission_mode not in VALID_CODEX_PERMISSION_MODES:
        raise ValueError(
            "OPENTAG_ZULIPMCP_CODEX_PERMISSION_MODE must be parity, "
            "workspace-write, read-only, or none"
        )
    max_sessions = _positive_int(
        env.get("OPENTAG_ZULIPMCP_MAX_SESSIONS", "1"),
        "OPENTAG_ZULIPMCP_MAX_SESSIONS",
    )
    idle_hours = _positive_float(
        env.get("OPENTAG_ZULIPMCP_IDLE_HOURS", "0.5"),
        "OPENTAG_ZULIPMCP_IDLE_HOURS",
    )
    runtime_dir = runtime_dir or root / ".runtime"
    mcp_path, prompt_path = prepare_runtime_files(
        root=root, runtime_dir=runtime_dir, idle_hours=idle_hours
    )
    log_dir = runtime_dir / "zulipmcp-logs"

    return [
        "uv",
        "run",
        "--with",
        ZULIPMCP_PACKAGE,
        "python3",
        str(root / "scripts" / "zulipmcp_entrypoint.py"),
        "--zuliprc",
        config_file,
        "--backend",
        backend,
        "--mcp-config",
        str(mcp_path),
        "--system-prompt",
        str(prompt_path),
        "--working-dir",
        workdir,
        "--log-dir",
        str(log_dir),
        "--codex-permission-mode",
        permission_mode,
        "--max-sessions",
        str(max_sessions),
    ]


def validate_runtime_paths(env: Mapping[str, str], *, engine: str) -> None:
    config_file = Path(_required(env, "ZULIP_CONFIG_FILE")).expanduser()
    if not config_file.is_file():
        raise ValueError(f"ZULIP_CONFIG_FILE does not exist: {config_file}")
    if engine == "zulipmcp":
        workdir = Path(_required(env, "OPENTAG_WORKDIR")).expanduser()
        if not workdir.is_dir():
            raise ValueError(f"OPENTAG_WORKDIR does not exist: {workdir}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch the configured OpenTag Zulip runtime.")
    parser.add_argument("--backend", choices=sorted(VALID_BACKENDS))
    parser.add_argument("--engine", choices=sorted(VALID_ENGINES))
    parser.add_argument(
        "--print-command",
        action="store_true",
        help="validate and print the secret-free launch command without starting it",
    )
    args = parser.parse_args()

    source_env = os.environ.copy()
    if args.backend:
        source_env["OPENTAG_BACKEND"] = args.backend
    if args.engine:
        source_env["OPENTAG_ZULIP_ENGINE"] = args.engine
    engine = selected_engine(source_env)
    validate_runtime_paths(source_env, engine=engine)
    if shutil.which("uv") is None:
        raise RuntimeError("uv is required to launch the Zulip runtime")
    command = build_runtime_command(source_env)
    if args.print_command:
        print(shlex.join(command))
        return 0

    print(f"Starting OpenTag Zulip engine={engine} backend={source_env['OPENTAG_BACKEND']}")
    os.execvpe(command[0], command, sanitized_environment(source_env, engine=engine))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
