#!/bin/sh
# Copyright 2026 Open Tag contributors
# SPDX-License-Identifier: Apache-2.0

set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
ENV_FILE=${OPENTAG_ENV_FILE:-$ROOT/.env}
RUNTIME_DIR=$ROOT/.runtime
SLACK_BOLT_SPEC=$(awk '/^slack-bolt==/ { print; exit }' "$ROOT/requirements-runtime.txt")
[ -n "$SLACK_BOLT_SPEC" ] || {
    printf 'requirements-runtime.txt does not pin slack-bolt.\n' >&2
    exit 1
}

usage() {
    printf '%s\n' \
        "Usage: ./tag <command>" \
        "" \
        "Commands:" \
        "  setup    Install prerequisites and create private configuration" \
        "  version  Show the Tag release version" \
        "  doctor   Run configuration and connectivity checks" \
        "  start    Start MFS, run preflight, and start the Slack bridge" \
        "  status   Show local service status" \
        "  stop     Stop the Slack bridge and local MFS server" \
        "  logs     Show the latest bridge logs"
}

load_config() {
    [ -f "$ENV_FILE" ] || {
        printf 'Missing configuration: %s\nRun ./tag setup first.\n' "$ENV_FILE" >&2
        exit 1
    }
    # The setup tool writes shell-quoted export statements with mode 0600.
    # shellcheck disable=SC1090
    . "$ENV_FILE"
}

process_start_time() {
    ps -p "$1" -o lstart= 2>/dev/null | sed 's/^ *//;s/ *$//'
}

process_command() {
    ps -p "$1" -o command= 2>/dev/null
}

record_pid() {
    pid_file=$1
    pid=$2
    marker=$3
    instance_id=${4:-}
    started=$(process_start_time "$pid")
    [ -n "$started" ] || return 1
    temporary_file=$pid_file.tmp.$$
    printf '%s\n%s\n%s\n%s\n' "$pid" "$started" "$marker" "$instance_id" \
        >"$temporary_file"
    mv "$temporary_file" "$pid_file"
}

pid_is_running() {
    pid_file=$1
    [ -f "$pid_file" ] || return 1
    pid=$(sed -n '1p' "$pid_file")
    expected_start=$(sed -n '2p' "$pid_file")
    marker=$(sed -n '3p' "$pid_file")
    instance_id=$(sed -n '4p' "$pid_file")
    case "$pid" in
        ''|*[!0-9]*) return 1 ;;
    esac
    [ -n "$expected_start" ] && [ -n "$marker" ] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    [ "$(process_start_time "$pid")" = "$expected_start" ] || return 1
    command_line=$(process_command "$pid")
    case "$command_line" in
        *"$marker"*) ;;
        *) return 1 ;;
    esac
    if [ -n "$instance_id" ]; then
        case "$command_line" in
            *"$instance_id"*) ;;
            *) return 1 ;;
        esac
    fi
    return 0
}

slack_is_ready() {
    pid_file=$RUNTIME_DIR/slack.pid
    ready_file=$RUNTIME_DIR/slack.ready
    pid_is_running "$pid_file" || return 1
    [ -f "$ready_file" ] || return 1

    expected_id=$(sed -n '4p' "$pid_file")
    ready_id=$(awk 'NR == 1 { print $1 }' "$ready_file")
    ready_pid=$(awk 'NR == 1 { print $2 }' "$ready_file")
    heartbeat=$(awk 'NR == 1 { print $3 }' "$ready_file")
    [ -n "$expected_id" ] && [ "$ready_id" = "$expected_id" ] || return 1
    case "$ready_pid" in
        ''|*[!0-9]*) return 1 ;;
    esac
    case "$heartbeat" in
        ''|*[!0-9]*) return 1 ;;
    esac
    kill -0 "$ready_pid" 2>/dev/null || return 1
    case "$(process_command "$ready_pid")" in
        *slack_socket_agent.py*) ;;
        *) return 1 ;;
    esac
    now=$(date +%s)
    [ $((now - heartbeat)) -le 5 ] || return 1
}

slack_status() {
    if slack_is_ready; then
        printf '✓ Slack bridge: connected (pid %s)\n' \
            "$(sed -n '1p' "$RUNTIME_DIR/slack.pid")"
        return 0
    fi
    if pid_is_running "$RUNTIME_DIR/slack.pid"; then
        printf '✗ Slack bridge: running but not connected\n'
    else
        printf '✗ Slack bridge: stopped\n'
    fi
    return 1
}

show_log_tail() {
    log_file=$1
    [ -f "$log_file" ] || return 0
    printf '\n==> %s (last 50 lines) <==\n' "$(basename "$log_file")" >&2
    tail -n 50 "$log_file" >&2
}

wait_for_mfs() {
    base=${MFS_URL:-http://127.0.0.1:13619}
    attempts=0
    while [ "$attempts" -lt 30 ]; do
        if curl -fsS "$base/healthz" >/dev/null 2>&1; then
            return 0
        fi
        attempts=$((attempts + 1))
        sleep 1
    done
    printf 'MFS did not become healthy at %s. Run ./tag logs.\n' "$base" >&2
    return 1
}

start_mfs() {
    base=${MFS_URL:-http://127.0.0.1:13619}
    if curl -fsS "$base/healthz" >/dev/null 2>&1; then
        return 0
    fi
    if command -v mfs >/dev/null 2>&1; then
        mfs serve start >/dev/null
    else
        nohup mfs-server run >"$ROOT/mfs-server.log" 2>&1 &
        mfs_pid=$!
        if ! record_pid "$RUNTIME_DIR/mfs.pid" "$mfs_pid" "mfs-server"; then
            kill "$mfs_pid" 2>/dev/null || true
            printf 'Could not record MFS process identity.\n' >&2
            return 1
        fi
    fi
    wait_for_mfs
}

run_doctor() {
    channel=${SLACK_CHANNEL_ID:-}
    if [ -n "$channel" ]; then
        python3 "$ROOT/scripts/opentag_doctor.py" --channel-id "$channel"
    else
        python3 "$ROOT/scripts/opentag_doctor.py"
    fi
}

start_slack() {
    if slack_is_ready; then
        return 0
    fi
    if pid_is_running "$RUNTIME_DIR/slack.pid"; then
        printf 'Slack bridge is already running but not connected. Run ./tag stop first.\n' >&2
        return 1
    fi

    rm -f "$RUNTIME_DIR/slack.pid" "$RUNTIME_DIR/slack.ready"
    instance_id=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
    nohup env OPENTAG_PROCESS_ID="$instance_id" \
        uv run --with "$SLACK_BOLT_SPEC" python3 "$ROOT/scripts/slack_socket_agent.py" \
        --backend "$OPENTAG_BACKEND" --ready-file "$RUNTIME_DIR/slack.ready" \
        --process-id "$instance_id" \
        >"$ROOT/opentag-slack-bridge.log" 2>&1 &
    slack_pid=$!
    if ! record_pid "$RUNTIME_DIR/slack.pid" "$slack_pid" "slack_socket_agent.py" "$instance_id"; then
        kill "$slack_pid" 2>/dev/null || true
        printf 'Could not record Slack bridge process identity.\n' >&2
        show_log_tail "$ROOT/opentag-slack-bridge.log"
        return 1
    fi
}

wait_for_slack() {
    attempts=0
    maximum_attempts=${OPENTAG_STARTUP_ATTEMPTS:-30}
    while [ "$attempts" -lt "$maximum_attempts" ]; do
        if slack_is_ready; then
            return 0
        fi
        if ! pid_is_running "$RUNTIME_DIR/slack.pid"; then
            printf 'Slack bridge exited before connecting to Socket Mode.\n' >&2
            show_log_tail "$ROOT/opentag-slack-bridge.log"
            return 1
        fi
        attempts=$((attempts + 1))
        sleep 1
    done
    printf 'Slack bridge did not connect to Socket Mode.\n' >&2
    show_log_tail "$ROOT/opentag-slack-bridge.log"
    return 1
}

stop_pid() {
    pid_file=$1
    if pid_is_running "$pid_file"; then
        kill "$(cat "$pid_file")"
    fi
    rm -f "$pid_file"
}

command=${1:-help}
case "$command" in
    setup)
        exec "$ROOT/install.sh"
        ;;
    version|--version)
        version=$(sed -n '1p' "$ROOT/VERSION")
        printf 'Tag v%s\n' "$version"
        ;;
    doctor)
        shift
        if [ "${1:-}" = "--offline" ]; then
            python3 "$ROOT/scripts/opentag_doctor.py" --offline
        else
            load_config
            run_doctor
        fi
        ;;
    start)
        load_config
        mkdir -p "$RUNTIME_DIR"
        chmod 0700 "$RUNTIME_DIR"
        if ! start_mfs; then
            show_log_tail "$ROOT/mfs-server.log"
            exit 1
        fi
        run_doctor
        if ! start_slack || ! wait_for_slack; then
            exit 1
        fi
        "$0" status
        ;;
    status)
        if [ -f "$ENV_FILE" ]; then
            load_config
        fi
        status_code=0
        if curl -fsS "${MFS_URL:-http://127.0.0.1:13619}/healthz" >/dev/null 2>&1; then
            printf '✓ MFS: healthy\n'
        else
            printf '✗ MFS: stopped or unhealthy\n'
            status_code=1
        fi
        slack_status || status_code=1
        exit "$status_code"
        ;;
    stop)
        mkdir -p "$RUNTIME_DIR"
        stop_pid "$RUNTIME_DIR/slack.pid"
        rm -f "$RUNTIME_DIR/slack.ready"
        stop_pid "$RUNTIME_DIR/mfs.pid"
        if command -v mfs >/dev/null 2>&1; then
            mfs serve stop >/dev/null 2>&1 || true
        fi
        "$0" status || true
        ;;
    logs)
        found=0
        for log in "$ROOT/mfs-server.log" "$ROOT/opentag-slack-bridge.log"; do
            if [ -f "$log" ]; then
                found=1
                printf '\n==> %s <==\n' "$(basename "$log")"
                tail -n 50 "$log"
            fi
        done
        if [ "$found" -eq 0 ]; then
            printf 'No Tag logs exist yet. Run ./tag start first.\n'
        fi
        ;;
    help|-h|--help)
        usage
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac
