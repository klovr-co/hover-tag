#!/bin/sh
# Print recent channel history and thread replies, using a test Tag's own bot token.
# Usage: read_thread.sh <tag-id> <channel-id> [limit]
set -eu
tag=$1 channel=$2 limit=${3:-20}
tok=$(python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(d.get('values',d)['SLACK_BOT_TOKEN'])" "$HOME/Tag/$tag/.tag/config/settings.json")
python3 - "$tok" "$channel" "$limit" <<'PY'
import json, subprocess, sys
tok, channel, limit = sys.argv[1:]
def api(method, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    out = subprocess.check_output(["curl", "-s", "-H", f"Authorization: Bearer {tok}", f"https://slack.com/api/{method}?{q}"])
    return json.loads(out)
names = {}
def who(m):
    u = m.get("user") or m.get("bot_id") or "?"
    if u not in names and m.get("user"):
        p = api("users.info", user=u).get("user", {})
        names[u] = p.get("real_name") or p.get("name") or u
    return names.get(u, u)
for m in reversed(api("conversations.history", channel=channel, limit=limit).get("messages", [])):
    print(f"[{who(m)}] {m.get('text', '')[:300]}")
    if m.get("reply_count"):
        for x in api("conversations.replies", channel=channel, ts=m["ts"]).get("messages", [])[1:]:
            cards = " [plan/task card]" if any(b.get("type") in ("plan", "task_card") for b in x.get("blocks") or []) else ""
            print(f"    └ [{who(x)}] {x.get('text', '')[:400]}{cards}")
PY
