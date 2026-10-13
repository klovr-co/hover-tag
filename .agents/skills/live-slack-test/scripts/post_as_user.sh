#!/bin/sh
# Post a message as the human tester, with the poster app's user token.
# Usage: post_as_user.sh <channel-id> <text> [thread-ts]
# Prints the new message's ts. Mention a Tag with <@BOT_USER_ID> in the text.
# The Tag must list the poster app in OPENTAG_TEST_APP_IDS (allow_test_poster.py).
set -eu
channel=$1 text=$2 thread=${3:-}
tok_file="$HOME/.config/tag-sandbox/slack-user-token"
[ -r "$tok_file" ] || { echo "Missing $tok_file (see SKILL.md: Post as the human)" >&2; exit 1; }
python3 - "$tok_file" "$channel" "$text" "$thread" <<'PY'
import json, sys, urllib.request
tok_file, channel, text, thread = sys.argv[1:]
body = {"channel": channel, "text": text}
if thread:
    body["thread_ts"] = thread
req = urllib.request.Request(
    "https://slack.com/api/chat.postMessage", data=json.dumps(body).encode(),
    headers={"Authorization": "Bearer " + open(tok_file).read().strip(),
             "Content-Type": "application/json; charset=utf-8"})
reply = json.load(urllib.request.urlopen(req))
if not reply.get("ok"):
    sys.exit(f"chat.postMessage failed: {reply.get('error')}")
print(reply["ts"])
PY
