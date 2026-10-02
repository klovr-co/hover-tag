// The one-time carry-over from the Swift Tag.app: verified before it's marked done.
import { describe, expect, it, vi } from "vitest";
import version from "../../protocol/examples/version.json";
import { migrateFromSwiftApp } from "./App";
import { demoBridge, type RunResult } from "./lib/bridge";

function fakeApi(keepRunning: (args: string[]) => boolean, capabilities = ["autostart", "autostart-keep"]) {
  const api = demoBridge();
  const calls: string[][] = [];
  const json = (value: unknown): RunResult => ({ code: 0, stdout: JSON.stringify(value), stderr: "" });
  api.tag = async (args) => {
    calls.push(args);
    if (args[0] === "version") return json({ ...version, capabilities });
    if (args[1] === "status") {
      return json({ enabled: true, tags: [{ tag: "a", keep_running: keepRunning(args) }, { tag: "b", keep_running: false }] });
    }
    return json({});
  };
  api.markMigrated = vi.fn(async () => {});
  return { api, calls };
}

describe("migrating from the Swift app", () => {
  it("keeps only Tags that still exist, turns on the login service, and records it", async () => {
    const { api, calls } = fakeApi(() => true);
    expect(await migrateFromSwiftApp(api, ["a", "renamed-long-ago"], ["a", "b"])).toBe(true);
    expect(calls).toContainEqual(["autostart", "keep", "a", "--json"]);
    expect(calls).toContainEqual(["autostart", "on", "--json"]);
    expect(api.markMigrated).toHaveBeenCalledOnce();
  });

  it("retries next launch when the result can't be verified", async () => {
    const { api } = fakeApi(() => false);
    expect(await migrateFromSwiftApp(api, ["a"], ["a"])).toBe(false);
    expect(api.markMigrated).not.toHaveBeenCalled();
  });

  it("waits for a Tag that can record choices without starting Tags", async () => {
    const { api, calls } = fakeApi(() => true, ["autostart"]);
    expect(await migrateFromSwiftApp(api, ["a"], ["a"])).toBe(false);
    expect(calls).toEqual([["version", "--json"]]);
  });
});
