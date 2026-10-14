#!/usr/bin/env python3
# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""Soak test for routines: a real bridge process, a scripted backend, a jumping clock.

    uv run --with slack-bolt==1.30.0 --with python-dateutil==2.9.0.post0 --with psutil==7.0.0 \\
        python tests/soak/routine_soak.py [--report report.json]

It needs no Slack and no AI account. The bridge child runs the real create_app,
scheduler, routine run path and backend subprocess handling; only Slack's Web
API (a recording client), Socket Mode (not started) and the agent (a scripted
fake opentag_agent.py) are stand-ins, and the clock is read from a file so the
test can jump it forward like a laptop waking up. The parent independently
works out which slots should have run and checks history, posts and backend
calls against that.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import tag_routines as routines  # noqa: E402

UTC = timezone.utc
OWNER = "U0OWNER01"
CHANNEL_A, CHANNEL_B = "C0AAAAAAAA", "C0BBBBBBBB"
START = datetime(2026, 10, 23, 0, 0, tzinfo=UTC)   # Friday; London leaves summer time on Oct 25, New York on Nov 1.
STEP = timedelta(minutes=5)
STEP_SECONDS = 0.15

ROUTINES = [
    {"name": "Digest", "schedule": "every 3h", "timezone": "UTC", "channel": CHANNEL_A},
    {"name": "London morning", "schedule": "daily 09:30", "timezone": "Europe/London", "channel": CHANNEL_A},
    {"name": "New York standup", "schedule": "weekdays 09:00", "timezone": "America/New_York", "channel": CHANNEL_A},
    {"name": "Quiet check", "schedule": "every 2h", "timezone": "UTC", "channel": CHANNEL_B},
    {"name": "Flaky", "schedule": "every 3h", "timezone": "UTC", "channel": CHANNEL_B},
    {"name": "Slow", "schedule": "every 2h", "timezone": "UTC", "channel": CHANNEL_B, "catch_up": "skip"},
]

FAKE_AGENT = r'''
import argparse, json, os, sys, time
parser = argparse.ArgumentParser()
parser.add_argument("--question", default="")
args, _ = parser.parse_known_args()
name = args.question.split('routine "', 1)[1].split('"', 1)[0]
log = os.environ["SOAK_BACKEND_LOG"]
def note(event):
    with open(log, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"routine": name, "event": event, "pid": os.getpid(), "at": time.time()}) + "\n")
note("start")
def emit(**event):
    print(json.dumps(event), flush=True)
if name == "Slow":
    time.sleep(float(os.environ.get("SOAK_SLOW_SECONDS", "5")))
if name == "Flaky":
    emit(type="error", text="scripted failure")
    note("end")
    sys.exit(1)
emit(type="usage", usage={"input_tokens": 1000, "output_tokens": 50})
text = "NO_REPLY" if name == "Quiet check" else f"{name} report"
emit(type="message_start", message_id="m1", phase="final_answer")
emit(type="message_complete", message_id="m1", phase="final_answer", text=text)
emit(type="turn_complete", status="completed")
note("end")
'''


# ---- Bridge child -------------------------------------------------------------------

def bridge(home: Path) -> None:
    """Run the real bridge code with a file clock, a recording Slack client and the fake agent."""
    from unittest import mock
    from scripts import agent_models, slack_socket_agent

    clock_file = home / "soak-clock.txt"
    posts = home / "soak-posts.jsonl"
    counter = [0]

    class Client:
        def chat_postMessage(self, **kwargs):
            counter[0] += 1
            ts = f"{int(time.time())}.{os.getpid() % 1000:03d}{counter[0]:03d}"
            with posts.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"channel": kwargs.get("channel"), "thread_ts": kwargs.get("thread_ts"),
                                         "text": kwargs.get("text"), "ts": ts}) + "\n")
            return {"ok": True, "ts": ts}

        def auth_test(self):
            return {"ok": True, "team_id": "T0SOAK001", "user_id": "U0BOT0001"}

        def __getattr__(self, name):
            return lambda **kwargs: {"ok": True, "channels": [], "members": [], "messages": []}

    class App:
        def __init__(self, *args, **kwargs):
            self.client = Client()
            import logging
            logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
            self.logger = logging.getLogger("soak")

        def __getattr__(self, name):
            return lambda *args, **kwargs: (lambda handler: handler)

        def use(self, *args, **kwargs):
            return None

    def now():
        return datetime.fromisoformat(clock_file.read_text(encoding="utf-8").strip())

    with mock.patch.object(slack_socket_agent, "App", App), \
            mock.patch.object(agent_models, "discover_codex_models", return_value=[]), \
            mock.patch.object(slack_socket_agent, "skill_dir", return_value=home / "fake-skill"), \
            mock.patch.object(slack_socket_agent, "queue_reply_summary", lambda *a, **k: None):
        app = slack_socket_agent.create_app("codex", 60, frozenset({OWNER}))
        scheduler = app.tag_routine_scheduler
        scheduler.clock = now
        scheduler.recover()
        (home / "soak-bridge-ready").write_text(str(os.getpid()), encoding="utf-8")
        while True:
            scheduler.tick()
            time.sleep(0.04)


# ---- Parent ---------------------------------------------------------------------------

class Soak:
    def __init__(self, workdir: Path) -> None:
        self.home = workdir / "home"
        self.home.mkdir()
        (self.home / "fake-skill/scripts").mkdir(parents=True)
        (self.home / "fake-skill/scripts/opentag_agent.py").write_text(FAKE_AGENT, encoding="utf-8")
        (workdir / "work").mkdir()
        self.env = {**os.environ, "TAG_INSTANCE_HOME": str(self.home), "OPENTAG_WORKDIR": str(workdir / "work"),
                    "SLACK_BOT_TOKEN": "xoxb-soak", "SLACK_TEAM_ID": "T0SOAK001",
                    "SLACK_CHANNEL_IDS": f"{CHANNEL_A},{CHANNEL_B}", "SLACK_ALLOWED_USER_IDS": OWNER,
                    "SOAK_BACKEND_LOG": str(self.home / "soak-backend.jsonl"), "OPENTAG_SLACK_STREAMING": "0",
                    "PYTHONPATH": str(ROOT)}
        self.now = START
        self.process: subprocess.Popen | None = None
        self.events: list[str] = []
        self.kills: list[dict] = []
        self.set_clock(START)
        store = routines.RoutineStore(self.home)
        for spec in ROUTINES:
            store.add({**spec, "prompt": f"Scripted soak routine {spec['name']}.", "owner": OWNER},
                      by="local-operator", via="cli", now=START)
        self.routines = {r["name"]: r for r in store.load()}

    def set_clock(self, moment: datetime) -> None:
        self.now = moment
        path = self.home / "soak-clock.txt"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(moment.isoformat(), encoding="utf-8")
        temporary.replace(path)

    def start_bridge(self) -> None:
        ready = self.home / "soak-bridge-ready"
        ready.unlink(missing_ok=True)
        log = (self.home / "soak-bridge.log").open("a", encoding="utf-8")
        self.process = subprocess.Popen([sys.executable, __file__, "bridge", str(self.home)], env=self.env,
                                        stdout=log, stderr=subprocess.STDOUT)
        deadline = time.time() + 60
        while not ready.exists():
            if self.process.poll() is not None or time.time() > deadline:
                raise RuntimeError("The soak bridge did not start; see soak-bridge.log")
            time.sleep(0.1)
        self.events.append(f"bridge started at {self.now.isoformat()}")

    def stop_bridge(self, sig: int = signal.SIGTERM) -> None:
        assert self.process is not None
        self.process.send_signal(sig)
        self.process.wait(timeout=30)
        self.process = None

    def run_for(self, duration: timedelta) -> None:
        end = self.now + duration
        while self.now < end:
            self.set_clock(min(self.now + STEP, end))
            time.sleep(STEP_SECONDS)

    def jump(self, duration: timedelta, label: str) -> None:
        self.events.append(f"{label}: clock jumped {duration} to {(self.now + duration).isoformat()}")
        self.set_clock(self.now + duration)
        time.sleep(1.0)

    def backend_calls(self) -> list[dict]:
        path = self.home / "soak-backend.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def kill_during_slow_run(self) -> None:
        """Kill -9 the bridge while a Slow run is in progress, then sleep for two hours."""
        seen = len([c for c in self.backend_calls() if c["routine"] == "Slow" and c["event"] == "start"])
        deadline = time.time() + 60
        while len([c for c in self.backend_calls() if c["routine"] == "Slow" and c["event"] == "start"]) == seen:
            if time.time() > deadline:
                raise RuntimeError("No Slow run started to interrupt")
            self.set_clock(self.now + STEP)
            time.sleep(STEP_SECONDS)
        time.sleep(0.5)
        state = routines.RoutineStateStore(self.home).load(self.routines["Slow"]["id"])
        self.kills.append({"at": self.now.isoformat(), "running": state.get("running")})
        self.stop_bridge(signal.SIGKILL)
        self.events.append(f"bridge killed (SIGKILL) during a Slow run at {self.now.isoformat()}")

    def settle(self) -> None:
        """Let running backends finish without advancing time, so the end state is quiet."""
        deadline = time.time() + 30
        states = routines.RoutineStateStore(self.home)
        while any(isinstance(states.load(r["id"]).get("running"), dict) for r in self.routines.values()):
            if time.time() > deadline:
                raise RuntimeError("Runs did not finish")
            time.sleep(0.2)
        time.sleep(0.5)


def expected_slots(routine: dict, start: datetime, end: datetime) -> list[datetime]:
    slots, moment = [], start
    while True:
        moment = routines.next_run(routine, moment)
        if moment > end:
            return slots
        slots.append(moment)


def check(soak: Soak) -> dict:
    states = routines.RoutineStateStore(soak.home)
    store = routines.RoutineStore(soak.home)
    current = {r["name"]: r for r in store.load()}
    posts = [json.loads(l) for l in (soak.home / "soak-posts.jsonl").read_text(encoding="utf-8").splitlines() if l]
    calls = soak.backend_calls()
    end = soak.now
    failures: list[str] = []
    summary: dict[str, dict] = {}
    ran_outcomes = {"posted", "no_reply", "failed", "budget_exceeded", "timeout", "interrupted"}
    for name, routine in soak.routines.items():
        state = states.load(routine["id"])
        history = state["history"]
        rid = routine["id"]
        outcomes: dict[str, int] = {}
        for entry in history:
            outcomes[entry["outcome"]] = outcomes.get(entry["outcome"], 0) + 1
        scheduled = [e for e in history if e["trigger"] in {"schedule", "catch-up"}]
        ran = [e for e in scheduled if e["outcome"] in ran_outcomes]
        ran_slots = [e["scheduled_for"] for e in ran]
        # 1. No slot ran twice.
        if len(ran_slots) != len(set(ran_slots)):
            failures.append(f"{name}: a slot ran twice")
        # 2. Every slot is accounted for exactly once: ran, skipped, blocked, or inside a missed entry.
        covered: list[str] = []
        for entry in scheduled:
            slot = datetime.fromisoformat(entry["scheduled_for"])
            if entry["outcome"] == "missed":
                block = [slot]
                for _ in range(entry.get("count", 1) - 1):
                    previous = block[0]
                    probe = previous - timedelta(days=40)
                    earlier = [s for s in expected_slots(routine, probe, previous) if s < previous]
                    block.insert(0, earlier[-1])
                covered.extend(s.isoformat(timespec="seconds") for s in block)
            else:
                covered.append(entry["scheduled_for"])
        first_due = routines.next_run(routine, START)
        paused_at = None
        if not current[name]["enabled"]:
            # Run records use the real clock; the soak clock is simulated, so use the slot instead.
            failed = [datetime.fromisoformat(e["scheduled_for"]) for e in history
                      if e["outcome"] == "failed" and e.get("scheduled_for")]
            paused_at = max(failed) if failed else end
        window_end = paused_at or end
        expected = [s.isoformat(timespec="seconds")
                    for s in [first_due] + expected_slots(routine, first_due, window_end)]
        if len(covered) != len(set(covered)):
            failures.append(f"{name}: a slot appears twice in history")
        missing = sorted(set(expected) - set(covered))
        extra = sorted(set(covered) - set(expected))
        if missing:
            failures.append(f"{name}: {len(missing)} slots unaccounted for, first {missing[0]}")
        if extra:
            failures.append(f"{name}: {len(extra)} history slots weren't due, first {extra[0]}")
        # 3. Posts match outcomes: one root post per posted or failed run, none for NO_REPLY.
        header = f"⏰ *{name}*"
        roots = [p for p in posts if p["thread_ts"] is None and p["text"].startswith(header)]
        replies = [p for p in roots if f"{name} report" in p["text"]]
        if len(replies) != outcomes.get("posted", 0):
            failures.append(f"{name}: {len(replies)} reply posts for {outcomes.get('posted', 0)} posted runs")
        notices = [p for p in roots if "didn't finish" in p["text"]]
        if len(notices) != outcomes.get("failed", 0):
            failures.append(f"{name}: {len(notices)} failure notices for {outcomes.get('failed', 0)} failed runs")
        if any(p["channel"] != routine["channel"] for p in roots):
            failures.append(f"{name}: posted outside its channel")
        # 4. Backend calls match runs that started (interrupted runs started too).
        started = len([c for c in calls if c["routine"] == name and c["event"] == "start"])
        executed = sum(outcomes.get(k, 0) for k in ran_outcomes)
        if started != executed:
            failures.append(f"{name}: {started} backend starts for {executed} runs")
        # 5. Nothing left running, and the next run is right.
        if state.get("running"):
            failures.append(f"{name}: still marked running")
        if current[name]["enabled"]:
            want = routines.next_run(current[name], end).isoformat()
            if state.get("next_due") != want:
                failures.append(f"{name}: next run {state.get('next_due')} should be {want}")
        # 6. Daylight saving: calendar routines ran at their local wall-clock time.
        if routine["schedule"]["kind"] != "interval":
            zone = ZoneInfo(routine["timezone"])
            for slot in ran_slots:
                local = datetime.fromisoformat(slot).astimezone(zone)
                if name == "London morning" and local.strftime("%H:%M") != "09:30":
                    failures.append(f"{name}: ran at {local.isoformat()}")
                if name == "New York standup" and (local.hour != 9 or local.weekday() > 4):
                    failures.append(f"{name}: ran at {local.isoformat()}")
        summary[name] = {"schedule": routine["schedule"]["text"], "expected_slots": len(expected),
                         "outcomes": outcomes, "backend_starts": started, "enabled": current[name]["enabled"],
                         "paused_reason": current[name].get("paused_reason")}
    flaky = current["Flaky"]
    if flaky["enabled"] or "failed runs in a row" not in (flaky.get("paused_reason") or ""):
        failures.append("Flaky: was not auto-paused")
    if not any("is paused after" in p["text"] for p in posts):
        failures.append("Flaky: no auto-pause notice")
    slow = states.load(soak.routines["Slow"]["id"])["history"]
    if not any(e["outcome"] == "interrupted" for e in slow):
        failures.append("Slow: the killed run was not recorded as interrupted")
    for kill in soak.kills:
        slot = (kill["running"] or {}).get("scheduled_for")
        if slot and len([e for e in slow if e.get("scheduled_for") == slot and e["outcome"] in ran_outcomes]) != 1:
            failures.append(f"Slow: the killed slot {slot} ran again or was lost")
    if not any(e["outcome"] == "skipped" for e in slow):
        failures.append("Slow: overlap never happened, so it wasn't tested")
    activity = list((soak.home / "state/activity").glob("*.json"))
    routine_activity = [p for p in activity if '"trigger"' in p.read_text(encoding="utf-8")]
    if len(routine_activity) > 50:
        failures.append(f"Activity kept {len(routine_activity)} routine runs, over 50")
    return {"ok": not failures, "failures": failures, "routines": summary, "events": soak.events,
            "posts": len(posts), "backend_calls": len([c for c in calls if c["event"] == "start"]),
            "activity_routine_records": len(routine_activity), "end": end.isoformat()}


def scenario(workdir: Path) -> dict:
    soak = Soak(workdir)
    soak.start_bridge()
    try:
        soak.run_for(timedelta(days=2))                  # Fri 23 → Sun 25: London leaves summer time
        soak.jump(timedelta(hours=9, minutes=10), "laptop asleep overnight")
        soak.run_for(timedelta(hours=6))
        soak.kill_during_slow_run()
        soak.set_clock(soak.now + timedelta(hours=2, minutes=20))   # dead for over two hours
        soak.start_bridge()
        soak.run_for(timedelta(days=1))
        soak.jump(timedelta(days=10), "laptop asleep for ten days")  # crosses New York's Nov 1 change
        soak.run_for(timedelta(days=1, hours=12))
        soak.settle()
    finally:
        if soak.process is not None:
            soak.stop_bridge()
    return check(soak)


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "bridge":
        bridge(Path(sys.argv[2]))
        return 0
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path)
    parser.add_argument("--keep", action="store_true", help="keep the soak home for inspection")
    args = parser.parse_args()
    workdir = Path(tempfile.mkdtemp(prefix="tag-routine-soak-"))
    started = time.time()
    report = scenario(workdir)
    report["seconds"] = round(time.time() - started, 1)
    report["home"] = str(workdir) if args.keep else None
    text = json.dumps(report, indent=2)
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    if not args.keep:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
