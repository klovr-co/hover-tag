#!/bin/zsh
# Modified by klovr.co in 2026 for Tag. See NOTICE and repository history.
# Open Tag's portable local control panel.

set -u

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
SLACK_BOLT_SPEC="$(awk '/^slack-bolt==/ { print; exit }' "$SKILL_DIR/requirements-runtime.txt")"
if [[ -z "$SLACK_BOLT_SPEC" ]]; then
  echo "requirements-runtime.txt does not pin slack-bolt."
  exit 1
fi
ENV_FILE="${OPENTAG_ENV_FILE:-$SKILL_DIR/.env}"
MFS_LOG="$SKILL_DIR/mfs-server.log"
SLACK_BRIDGE_LOG="$SKILL_DIR/opentag-slack-bridge.log"
ZULIP_BRIDGE_LOG="$SKILL_DIR/opentag-zulip-bridge.log"

is_mfs_running() {
  pgrep -f "mfs-server run" >/dev/null 2>&1
}

is_slack_running() {
  pgrep -f "$SKILL_DIR/scripts/slack_socket_agent.py --backend" >/dev/null 2>&1
}

is_native_zulip_running() {
  pgrep -f "$SKILL_DIR/scripts/zulip_agent.py --backend" >/dev/null 2>&1
}

is_zulipmcp_running() {
  pgrep -f "$SKILL_DIR/scripts/zulipmcp_entrypoint.py --zuliprc" >/dev/null 2>&1
}

is_zulip_running() {
  is_native_zulip_running || is_zulipmcp_running
}

show_status() {
  echo
  if is_mfs_running; then
    echo "✓ MFS memory server: running"
  else
    echo "✗ MFS memory server: stopped"
  fi
  if is_slack_running; then
    echo "✓ Open Tag Slack bridge: running"
  else
    echo "✗ Open Tag Slack bridge: stopped"
  fi
  if is_zulipmcp_running; then
    echo "✓ Open Tag Zulip bridge: running (zulipmcp)"
  elif is_native_zulip_running; then
    echo "✓ Open Tag Zulip bridge: running (native)"
  else
    echo "✗ Open Tag Zulip bridge: stopped"
  fi
  echo
}

require_config() {
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "Missing Open Tag configuration:"
    echo "  $ENV_FILE"
    echo
    echo "Copy .env.example to .env and fill in your chat/MFS values."
    return 1
  fi
}

start_opentag() {
  require_config || return 1
  source "$ENV_FILE"

  if ! is_mfs_running; then
    echo "Starting MFS memory server…"
    nohup mfs-server run >"$MFS_LOG" 2>&1 &
  fi

  local transport="${OPENTAG_TRANSPORT:-slack}"
  if [[ "$transport" != "slack" && "$transport" != "zulip" && "$transport" != "both" ]]; then
    echo "OPENTAG_TRANSPORT must be slack, zulip, or both."
    return 1
  fi

  if [[ "$transport" == "slack" || "$transport" == "both" ]]; then
    if is_slack_running; then
      echo "Open Tag Slack bridge is already running."
    elif [[ -z "${SLACK_APP_TOKEN:-}" || -z "${SLACK_BOT_TOKEN:-}" ]]; then
      echo "Slack bridge not started: set SLACK_APP_TOKEN and SLACK_BOT_TOKEN in $ENV_FILE."
    else
      echo "Starting Open Tag Slack bridge…"
      (
        exec uv run --with "$SLACK_BOLT_SPEC" python3 "$SKILL_DIR/scripts/slack_socket_agent.py" \
          --backend "${OPENTAG_BACKEND:?OPENTAG_BACKEND is required}"
      ) >"$SLACK_BRIDGE_LOG" 2>&1 &
    fi
  fi

  if [[ "$transport" == "zulip" || "$transport" == "both" ]]; then
    if is_zulip_running; then
      echo "Open Tag Zulip bridge is already running."
    elif [[ -z "${ZULIP_CONFIG_FILE:-}" ]]; then
      echo "Zulip bridge not started: set ZULIP_CONFIG_FILE in $ENV_FILE."
    else
      echo "Starting Open Tag Zulip bridge…"
      (
        exec python3 "$SKILL_DIR/scripts/zulip_runtime.py" \
          --backend "${OPENTAG_BACKEND:?OPENTAG_BACKEND is required}"
      ) >"$ZULIP_BRIDGE_LOG" 2>&1 &
    fi
  fi

  sleep 2
  show_status
}

stop_opentag() {
  if is_slack_running; then
    pkill -f "$SKILL_DIR/scripts/slack_socket_agent.py --backend"
  fi
  if is_native_zulip_running; then
    pkill -f "$SKILL_DIR/scripts/zulip_agent.py --backend"
  fi
  if is_zulipmcp_running; then
    pkill -f "$SKILL_DIR/scripts/zulipmcp_entrypoint.py --zuliprc"
  fi
  if is_mfs_running; then
    pkill -f "mfs-server run"
  fi
  sleep 1
  show_status
}

while true; do
  clear
  echo "Open Tag Control"
  echo "================"
  show_status
  echo "1) Start Open Tag"
  echo "2) Check status"
  echo "3) Stop Open Tag"
  echo "4) Open logs folder"
  echo "q) Quit"
  echo
  read "choice?Choose an option: "

  case "$choice" in
    1) start_opentag ;;
    2) show_status ;;
    3) stop_opentag ;;
    4) open "$SKILL_DIR" ;;
    q|Q) exit 0 ;;
    *) echo "Please choose 1–4 or q." ;;
  esac

  echo
  read "?Press Return to continue…"
done
