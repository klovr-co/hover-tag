"""Opt-in, report-only Codex diagnosis. Never forward raw configuration or logs."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


SIGNALS = {
    "BrokenPipeError": "socket_broken_pipe_observed",
    "missing_scope": "missing_slack_permission_observed",
    "invalid_auth": "slack_auth_rejected_observed",
    "ModuleNotFoundError": "python_dependency_missing_observed",
    "codex executable missing": "backend_executable_missing_observed",
    "MFS did not become healthy": "memory_startup_timeout_observed",
}


def report(doctor_exit, slack, memory, logs):
    signals = set()
    for path in logs:
        try:
            with Path(path).open("rb") as stream:
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell() - 32768))
                tail = stream.read(32768).decode("utf-8", errors="replace")
        except OSError:
            continue
        signals.update(label for pattern, label in SIGNALS.items() if pattern in tail)
    return {"doctor_passed": doctor_exit == 0, "slack_connected": bool(slack),
            "memory_healthy": bool(memory), "historical_log_signals": sorted(signals),
            "note": "Log signals may be old; they do not establish the current cause. First reply not verified."}


def offer(payload):
    if not sys.stdin.isatty():
        return
    print("\nOptional Codex diagnosis · suggestions only")
    print("Only this report will be supplied (no raw logs, tokens, or Slack messages):")
    safe_report = json.dumps(payload, indent=2)
    print(safe_report)
    print("Uses your Codex sign-in and may incur usage. No fixes or restarts are requested.")
    print("Read-only mode and disabled shell tools are safeguards, not a hardened isolation boundary.")
    try:
        if input("Send this report to Codex? [y/N]: ").strip().lower() not in {"y", "yes"}:
            return
    except (EOFError, KeyboardInterrupt):
        print("\nSkipped Codex diagnosis.")
        return
    executable = shutil.which("codex")
    if not executable:
        print("Codex was not found. Use tag logs, or install and sign in to Codex first.")
        return
    # Only retain runtime essentials and Codex authentication, never Slack/MFS env.
    environment = {key: value for key, value in os.environ.items() if key in {
        "PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR",
        "LANG", "LC_ALL", "CODEX_HOME", "OPENAI_API_KEY",
    }}
    try:
        help_result = subprocess.run([executable, "exec", "--help"], env=environment,
                                     capture_output=True, text=True, timeout=10)
        if help_result.returncode or any(flag not in help_result.stdout for flag in (
            "--ignore-user-config", "--ephemeral", "--sandbox", "--disable",
        )):
            print("This Codex version lacks required safeguards. Update Codex before retrying.")
            return
        with tempfile.TemporaryDirectory(prefix="tag-diagnosis-") as directory:
            command = [executable, "exec", "--ignore-user-config", "--ephemeral",
                       "--sandbox", "read-only", "--disable", "shell_tool",
                       "--disable", "unified_exec", "--skip-git-repo-check",
                       "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
                       "--color", "never", "-"]
            prompt = ("Diagnose Tag using ONLY the report below. Do not use tools, inspect files, "
                      "execute commands, change configuration, send messages, or restart services. "
                      "Write like a friendly teammate helping the user, not an audit report. "
                      "Use 2 short paragraphs, at most 80 words, plain text without headings or bullets. "
                      "Lead with what is working or the current problem, then offer one simple next step. "
                      "Use everyday language and contractions; avoid JSON field names and phrases like "
                      "'the report establishes', 'historical signals', or 'first reply verification'. "
                      "Separate known facts from guesses in natural language. Old errors may be from earlier; "
                      "do not describe them as a current failure without evidence. Never request secrets. "
                      "Do not claim everything works just because services are connected; a reply still needs testing. "
                      "When all checks pass, an appropriate tone is: Everything looks healthy right now—"
                      "Tag is connected to Slack and memory is ready. Those errors may be from earlier. "
                      "Then suggest mentioning Tag in the configured channel with a harmless greeting "
                      "to check that it replies. Adapt this wording to the actual checks; never copy a "
                      "healthy example when a check fails.\n" + safe_report)
            result = subprocess.run(command, input=prompt, env=environment, cwd=directory,
                                    text=True, capture_output=True, timeout=120)
            if result.returncode:
                print("Codex diagnosis failed. Check Codex sign-in locally; tag logs remains available.")
            else:
                print("\nHere’s what Codex suggests:\n" + result.stdout)
                print("\nNothing was changed. Suggestions still need checking.")
    except subprocess.TimeoutExpired:
        print("Codex diagnosis timed out. No fixes were requested.")
    except OSError:
        print("Could not launch Codex. Use tag logs to continue troubleshooting.")
