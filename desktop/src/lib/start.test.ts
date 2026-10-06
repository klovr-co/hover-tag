// Starting a Tag reads as a few plain steps, from what `tag NAME start --json` reports.
import { describe, expect, it } from "vitest";
import example from "../../../protocol/examples/start-progress.jsonl?raw";
import { initialStart, phaseOf, startReducer, startResult } from "./start";

const lines = example.trim().split("\n");
const states = (lines: string[]) => {
  const progress = lines.reduce(startReducer, initialStart());
  return [Object.fromEntries(Object.entries(progress.phases).map(([id, p]) => [id, p.state])), progress.current];
};

describe("start progress", () => {
  it("groups Tag's readiness rows into steps", () => {
    expect(["runtime", "slack-app", "picture", "workspace", "start"].map(phaseOf)).toEqual(Array(5).fill("prepare"));
    expect(["memory", "channel-memory", "checks", "slack"].map(phaseOf)).toEqual(["memory", "channels", "slack", "slack"]);
  });

  it("marks earlier steps done as later ones begin, and the running one as current", () => {
    const channels = lines.findIndex((line) => line.includes('"channel-memory"'));
    expect(states(lines.slice(0, channels + 1))).toEqual([
      { prepare: "done", memory: "done", channels: "running", slack: "pending" }, "channels",
    ]);
    expect(states(lines.slice(0, -1))).toEqual([{ prepare: "done", memory: "done", channels: "done", slack: "done" }, null]);
  });

  it("keeps a step that needs attention, and ignores what isn't progress", () => {
    const warned = startReducer(initialStart(), JSON.stringify({ type: "progress", step: "picture", label: "Picture", state: "attention", text: "Waits for users:read" }));
    expect(startReducer(warned, '{"type": "progress", "step": "memory", "label": "Memory", "state": "running", "text": "…"}').phases.prepare.state).toBe("attention");
    expect(startReducer(warned, "not json")).toBe(warned);
  });

  it("finishes the channel step at once when channels keep importing in the background", () => {
    const progress = [
      '{"type": "progress", "step": "channel-memory", "label": "Channel memory", "state": "running", "text": "Checking…"}',
      '{"type": "progress", "step": "channel-memory", "label": "Channel memory", "state": "info", "text": "Importing #prod-hover in the background"}',
    ].reduce(startReducer, initialStart());
    expect(progress.phases.channels).toEqual({ state: "done", text: "Importing #prod-hover in the background", background: true });
    expect(progress.current).toBeNull();
  });

  it("keeps a later row Tag doesn't group, such as the welcome DM, from reopening the first step", () => {
    const after = startReducer(lines.slice(0, -1).reduce(startReducer, initialStart()),
      '{"type": "progress", "step": "welcome-dm", "label": "Welcome DM", "state": "info", "text": "Sent"}');
    expect(Object.values(after.phases).map((p) => p.state)).toEqual(["done", "done", "done", "done"]);
    expect(after.current).toBeNull();
  });

  it("reads the outcome, including a failure before progress began", () => {
    expect(startResult(lines[lines.length - 1])).toEqual({ ok: true, error: "" });
    expect(startResult('{"type": "result", "status": "failed", "error": "Slack bridge did not become ready"}'))
      .toEqual({ ok: false, error: "Slack bridge did not become ready" });
    expect(startResult('{"schema_version": 1, "ok": false, "error": "Unknown Tag"}')).toEqual({ ok: false, error: "Unknown Tag" });
    expect(startResult(lines[0])).toBeNull();
  });
});
