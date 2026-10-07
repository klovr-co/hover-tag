// Usage telemetry: one installation-wide choice, fixed events, nothing before the notice.
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Bridge, RunResult } from "./bridge";
import { initialSetup, type SetupState } from "./setup";
import { needsNotice, recordArgs, SetupTracker, useTelemetry, type TelemetryStatus } from "./telemetry";

const status = (overrides: Partial<TelemetryStatus> = {}): TelemetryStatus => ({
  enabled: false, available: true, saved_preference: "not_set", process_override: null,
  privacy_notice: "https://example.invalid/privacy", ...overrides,
});
const json = (value: unknown): RunResult => ({ code: 0, stdout: JSON.stringify(value), stderr: "" });

function fakeTag(initial: TelemetryStatus, capabilities = ["list", "telemetry-events"]) {
  let current = initial;
  const tag = vi.fn(async (args: string[]): Promise<RunResult> => {
    if (args[0] === "version") return json({ version: "0.3.0", capabilities });
    if (args[1] === "on" || args[1] === "off") current = status({ enabled: args[1] === "on", saved_preference: args[1] });
    if (args[1] === "record") return json({ ok: true });
    return json(current);
  });
  return { api: { tag } as unknown as Bridge, tag };
}

const recorded = (tag: ReturnType<typeof fakeTag>["tag"]) => tag.mock.calls.map(([args]) => args).filter((args) => args[1] === "record");

describe("the notice", () => {
  it("shows only when collection is possible and nobody has chosen", () => {
    expect(needsNotice(status())).toBe(true);
    expect(needsNotice(null)).toBe(false);
    expect(needsNotice(status({ saved_preference: "off" }))).toBe(false);
    expect(needsNotice(status({ saved_preference: "on", enabled: true }))).toBe(false);
    expect(needsNotice(status({ process_override: "off" }))).toBe(false);
    expect(needsNotice(status({ available: false }))).toBe(false);
    expect(needsNotice(status({ privacy_notice: null }))).toBe(false);
  });
});

describe("recording", () => {
  it("passes whole seconds and closed values to tag telemetry record", () => {
    expect(recordArgs("app_setup_step_completed", { step: "ai", elapsed_seconds: 12.6 }))
      .toEqual(["telemetry", "record", "app_setup_step_completed", "step=ai", "elapsed_seconds=13"]);
  });

  it("turns usage data on at first run, says so once, then records through the CLI", async () => {
    const { api, tag } = fakeTag(status());
    const { result } = renderHook(() => useTelemetry(api, true));
    await waitFor(() => expect(result.current.announced).toBe(true));
    expect(tag).toHaveBeenCalledWith(["telemetry", "on", "--json"]);
    expect(result.current.recording).toBe(true);
    result.current.track("app_screen_viewed", { screen: "home" });
    expect(recorded(tag)).toEqual([["telemetry", "record", "app_screen_viewed", "screen=home"]]);

    act(() => result.current.acknowledge());
    expect(result.current.announced).toBe(false);
  });

  it("keeps an earlier choice to turn usage data off", async () => {
    const { api, tag } = fakeTag(status({ saved_preference: "off", enabled: false }));
    const { result } = renderHook(() => useTelemetry(api, true));
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(tag).not.toHaveBeenCalledWith(["telemetry", "on", "--json"]);
    expect(result.current.announced).toBe(false);
    result.current.track("app_screen_viewed", { screen: "home" });
    expect(recorded(tag)).toEqual([]);
  });

  it("never records with a Tag that doesn't accept app events", async () => {
    const { api, tag } = fakeTag(status({ enabled: true, saved_preference: "on" }), ["list"]);
    const { result } = renderHook(() => useTelemetry(api, true));
    await waitFor(() => expect(result.current.loaded).toBe(true));
    result.current.track("app_screen_viewed", { screen: "home" });
    expect(result.current.recording).toBe(false);
    expect(recorded(tag)).toEqual([]);
  });

  it("records nothing and says nothing when Tag can't save the first-run choice", async () => {
    const { api, tag } = fakeTag(status());
    tag.mockImplementation(async (args) => args[1] === "on" ? { code: 1, stdout: "", stderr: "Error: disk full" }
      : args[0] === "version" ? json({ capabilities: ["telemetry-events"] }) : json(status()));
    const { result } = renderHook(() => useTelemetry(api, true));
    await waitFor(() => expect(tag).toHaveBeenCalledWith(["telemetry", "on", "--json"]));
    expect(result.current.announced).toBe(false);
    expect(result.current.recording).toBe(false);
    expect(result.current.status?.saved_preference).toBe("not_set");
  });

  it("doesn't wait for a status when Tag isn't installed", () => {
    const { api, tag } = fakeTag(status());
    const { result } = renderHook(() => useTelemetry(api, false));
    expect(result.current.loaded).toBe(true);
    expect(tag).not.toHaveBeenCalled();
  });
});

describe("setup tracking", () => {
  const at = (id: string, extra: Partial<SetupState> = {}): SetupState => ({ ...initialSetup, question: { type: "question", id, kind: "choose", prompt: "" }, ...extra });

  function tracker(entry: "first_tag" | "add_tag" = "add_tag") {
    let now = 0;
    const track = vi.fn();
    const setup = new SetupTracker(track, entry, () => now);
    return { setup, track, tick: (seconds: number) => { now += seconds * 1000; } };
  }

  it("completes a step only when moving forward and times it from when it was entered", () => {
    const { setup, track, tick } = tracker();
    setup.start();
    setup.observe(at("profile"));
    tick(4);
    setup.observe(at("default_model"));
    tick(2);
    setup.observe(at("profile")); // back
    tick(1);
    setup.observe(at("default_model"));
    tick(9);
    setup.observe(at("workspace"));
    expect(track.mock.calls).toEqual([
      ["app_setup_started", { entry_point: "add_tag" }],
      ["app_setup_step_completed", { step: "tag", elapsed_seconds: 4 }],
      ["app_setup_step_completed", { step: "tag", elapsed_seconds: 1 }],
      ["app_setup_step_completed", { step: "ai", elapsed_seconds: 9 }],
    ]);
  });

  it("reports an abandoned setup once, with why it ended", () => {
    const { setup, track, tick } = tracker();
    setup.start();
    setup.observe(at("workspace"));
    tick(30);
    setup.abandon("cancelled");
    setup.observe({ ...at("workspace"), outcome: "paused" });
    expect(track.mock.calls.slice(1)).toEqual([
      ["app_setup_abandoned", { last_step: "workspace", reason: "cancelled", elapsed_seconds: 30 }],
    ]);
  });

  it("counts a failed setup as abandoned, and names the existing-app steps", () => {
    const { setup, track } = tracker("first_tag");
    setup.start();
    setup.observe(at("app_id", { existing: true }));
    setup.observe({ ...at("app_id", { existing: true }), outcome: "failed" });
    expect(track.mock.calls[1]).toEqual(["app_setup_abandoned", { last_step: "app", reason: "failed", elapsed_seconds: 0 }]);
  });
});
