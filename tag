#!/bin/sh
# Copyright 2026 Open Tag contributors
# SPDX-License-Identifier: Apache-2.0

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
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
        "  start    Start MFS, run preflight, and start configured bridges" \
        "  status   Show local service status" \
        "  stop     Stop bridges and the local MFS server" \
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

pid_is_running() {
    [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null
}

service_status() {
    label=$1
    pid_file=$2
    if pid_is_running "$pid_file"; then
        printf '✓ %s: running (pid %s)\n' "$label" "$(cat "$pid_file")"
    else
        printf '✗ %s: stopped\n' "$label"
    fi
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
        printf '%s\n' "$!" >"$RUNTIME_DIR/mfs.pid"
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
    if pid_is_running "$RUNTIME_DIR/slack.pid"; then
        return 0
    fi
    nohup uv run --with "$SLACK_BOLT_SPEC" python3 "$ROOT/scripts/slack_socket_agent.py" \
        --backend "$OPENTAG_BACKEND" >"$ROOT/opentag-slack-bridge.log" 2>&1 &
    printf '%s\n' "$!" >"$RUNTIME_DIR/slack.pid"
}

start_zulip() {
    if pid_is_running "$RUNTIME_DIR/zulip.pid"; then
        return 0
    fi
    nohup python3 "$ROOT/scripts/zulip_runtime.py" --backend "$OPENTAG_BACKEND" \
        >"$ROOT/opentag-zulip-bridge.log" 2>&1 &
    printf '%s\n' "$!" >"$RUNTIME_DIR/zulip.pid"
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
        start_mfs
        run_doctor
        transport=${OPENTAG_TRANSPORT:-slack}
        case "$transport" in
            slack) start_slack ;;
            zulip) start_zulip ;;
            both) start_slack; start_zulip ;;
            *) printf 'OPENTAG_TRANSPORT must be slack, zulip, or both.\n' >&2; exit 1 ;;
        esac
        sleep 2
        "$0" status
        ;;
    status)
        if [ -f "$ENV_FILE" ]; then
            load_config
        fi
        if curl -fsS "${MFS_URL:-http://127.0.0.1:13619}/healthz" >/dev/null 2>&1; then
            printf '✓ MFS: healthy\n'
        else
            printf '✗ MFS: stopped or unhealthy\n'
        fi
        service_status "Slack bridge" "$RUNTIME_DIR/slack.pid"
        service_status "Zulip bridge" "$RUNTIME_DIR/zulip.pid"
        ;;
    stop)
        mkdir -p "$RUNTIME_DIR"
        stop_pid "$RUNTIME_DIR/slack.pid"
        stop_pid "$RUNTIME_DIR/zulip.pid"
        stop_pid "$RUNTIME_DIR/mfs.pid"
        if command -v mfs >/dev/null 2>&1; then
            mfs serve stop >/dev/null 2>&1 || true
        fi
        "$0" status
        ;;
    logs)
        found=0
        for log in "$ROOT/mfs-server.log" "$ROOT/opentag-slack-bridge.log" "$ROOT/opentag-zulip-bridge.log"; do
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
