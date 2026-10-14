import { describe, expect, test } from "vitest";
import list from "../../../protocol/examples/list.json";
import { baseMood, moment, parseNow, type Now } from "./mood";
import type { TagRow } from "./protocol";

const row = (state: string): TagRow => ({ ...(list.tags[0] as TagRow), state });
const run = (id: string) => ({ run_id: id, thread: "t1", started_at: "2026-10-13T10:00:00+00:00" });
const now = (running: string[], last: [string, string] | null = null): Now =>
  ({ running: running.map(run), last: last && { run_id: last[0], thread: "t1", outcome: last[1], at: "2026-10-13T10:01:00+00:00" } });

describe("what a Tag's waterdrop plays", () => {
  test("its state comes first", () => {
    expect(baseMood(row("running"))).toBe("idle");
    expect(baseMood(row("stopped"))).toBe("off");
    expect(baseMood(row("setup_incomplete"))).toBe("setup");
    expect(baseMood(row("needs_attention"))).toBe("attn");
    expect(baseMood(row("stopped"), { busy: true })).toBe("start");
    expect(baseMood(row("running"), { problem: "Codex isn't signed in" })).toBe("attn");
  });

  test("a running request makes it think, from activity-now.json or the activity read", () => {
    expect(baseMood(row("running"), { now: now(["a"]) })).toBe("work");
    expect(baseMood(row("running"), { now: now([]) })).toBe("idle");
    expect(baseMood(row("running"), { latest: { kind: "working", at: "", channel: "C1", channel_name: null, dm: false } })).toBe("work");
    // activity-now.json is newer than the activity read, so it wins.
    expect(baseMood(row("running"), { now: now([]), latest: { kind: "working", at: "", channel: "C1", channel_name: null, dm: false } })).toBe("idle");
    // A stopped Tag sleeps, whatever an old file says.
    expect(baseMood(row("stopped"), { now: now(["a"]) })).toBe("off");
  });

  test("a new request and a finished one each play a moment once", () => {
    expect(moment(null, now(["a"]))).toBeNull();
    expect(moment(now([]), now(["a"]))).toBe("mention");
    expect(moment(now(["a"]), now([], ["a", "completed"]))).toBe("reply");
    expect(moment(now(["a"]), now([], ["a", "failed"]))).toBe("attn");
    expect(moment(now(["a"]), now([], ["a", "interrupted"]))).toBeNull();
    expect(moment(now([], ["a", "completed"]), now([], ["a", "completed"]))).toBeNull();
  });

  test("reads only well-formed files", () => {
    expect(parseNow({ running: [run("a"), { nope: 1 }], last: null })).toEqual({ running: [run("a")], last: null });
    expect(parseNow({ schema_version: 1 })).toBeNull();
    expect(parseNow("x")).toBeNull();
  });
});
