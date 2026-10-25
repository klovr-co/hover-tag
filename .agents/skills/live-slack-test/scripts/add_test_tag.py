"""Create one test Tag in the Slack sandbox by driving `./tag add --json`.

Usage (from the repo root): python3 add_test_tag.py <name> <codex|claude> <org-id> <workspace-id>

Take both IDs from ~/.config/tag-sandbox/sandbox.md.
"""
import json, subprocess, sys
name, backend = sys.argv[1], sys.argv[2]
ORG, WORKSPACE = sys.argv[3], sys.argv[4]
proc = subprocess.Popen(["./tag", "add", "--json"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
def send(value): proc.stdin.write(json.dumps(value) + "\n"); proc.stdin.flush()
def idx(q, wanted):
    ids = q.get("option_ids") or []
    for w in wanted:
        for i, o in enumerate(ids):
            if o == w or (w.endswith("*") and o.startswith(w[:-1])): return i
    return None
for line in proc.stdout:
    try: ev = json.loads(line)
    except ValueError: continue
    print(json.dumps({k: ev.get(k) for k in ("type","id","prompt","text","status","error","option_ids","ready") if ev.get(k) is not None})[:500], flush=True)
    if ev.get("type") == "result": break
    if ev.get("type") != "question": continue
    q = ev["id"]
    if q == "profile":
        send({"answer": {"name": name, "description": "Temporary test Tag for handoffs."}})
    elif q == "ai_connection":
        send({"answer": idx(ev, [backend, "skip", "continue"]) or 0})
    elif q == "default_model":
        send({"answer": idx(ev, [backend])})
    elif q == "workspace":
        send({"answer": idx(ev, [ORG])})
    elif q == "org_workspace":
        i = idx(ev, [WORKSPACE]); send({"answer": i if i is not None else idx(ev, ["manual"])})
    elif q == "org_workspace_id":
        send({"answer": WORKSPACE})
    elif q == "approve_setup":
        send({"answer": idx(ev, ["create"])})
    elif q == "channels":
        send({"answer": []})
    else:
        print("UNHANDLED", json.dumps(ev)[:800], flush=True); send({"answer": None, "pause": True})
proc.wait(); print("exit", proc.returncode)
