import { describe, expect, it, vi } from "vitest";
import { demoBridge } from "./bridge";
import { checkUpdate, installUpdate } from "./updates";

function fixture(current = "0.2.0", target = "0.3.0", app: string | null = target) {
  const api = demoBridge();
  let installed = current;
  const events: string[] = [];
  api.checkAppUpdate = vi.fn(async () => app ? { version: app } : null);
  api.installAppUpdate = vi.fn(async () => { events.push("desktop"); });
  api.tag = vi.fn(async (args: string[]) => {
    if (args[0] === "version") return { code: 0, stderr: "", stdout: JSON.stringify({ version: installed }) };
    const dry = args.includes("--dry-run");
    const before = installed;
    if (!dry) { installed = target; events.push("runtime"); }
    return { code: 0, stderr: "", stdout: JSON.stringify({ ok: true,
      status: dry ? (installed === target ? "current" : "available") : "upgraded",
      current: { version: before }, target: { version: target } }) };
  });
  return { api, events };
}

describe("one Tag update", () => {
  it("updates and verifies the runtime before restarting the desktop", async () => {
    const { api, events } = fixture();
    expect(await installUpdate(api, "0.2.0")).toMatchObject({ current: "0.3.0", runtime: false, desktop: false });
    expect(events).toEqual(["runtime", "desktop"]);
    expect(api.installAppUpdate).toHaveBeenCalledWith("0.3.0");
    expect(api.tag).toHaveBeenCalledWith(["upgrade", "--json"]); // Retains saved policy.
  });
  it("aligns an older runtime with an already updated app", async () => {
    const { api, events } = fixture("0.2.0", "0.3.0", null);
    await installUpdate(api, "0.3.0");
    expect(events).toEqual(["runtime"]);
  });
  it("aligns the app after a CLI-only upgrade", async () => {
    const { api, events } = fixture("0.3.0");
    await installUpdate(api, "0.2.0");
    expect(events).toEqual(["desktop"]);
  });
  it.each([null, "0.4.0"])("leaves the installation alone without matching desktop artifacts (%s)", async (app) => {
    const { api, events } = fixture("0.2.0", "0.3.0", app);
    await expect(installUpdate(api, "0.2.0")).rejects.toThrow("complete Tag update");
    expect(events).toEqual([]);
  });
  it("retries a desktop failure without repeating the runtime upgrade", async () => {
    const { api, events } = fixture();
    vi.mocked(api.installAppUpdate).mockRejectedValueOnce(new Error("Download failed"));
    await expect(installUpdate(api, "0.2.0")).rejects.toThrow("Download failed");
    await installUpdate(api, "0.2.0");
    expect(events).toEqual(["runtime", "desktop"]);
  });
  it("does not reinstall an already aligned release", async () => {
    const { api, events } = fixture("0.3.0", "0.3.0", null);
    expect(await checkUpdate(api, "0.3.0")).toMatchObject({ runtime: false, desktop: false });
    await installUpdate(api, "0.3.0");
    expect(events).toEqual([]);
  });
  it("respects an exact version pin even when a newer app is published", async () => {
    const { api } = fixture();
    api.tag = vi.fn(async () => ({ code: 0, stderr: "", stdout: JSON.stringify({ ok: true, status: "pinned", current: { version: "0.2.0" } }) }));
    expect(await checkUpdate(api, "0.2.0")).toMatchObject({ version: "0.2.0", runtime: false, desktop: false });
  });
  it("keeps the desktop running when the runtime upgrade fails", async () => {
    const { api } = fixture();
    const tag = api.tag;
    api.tag = vi.fn(async (args) => args[0] === "upgrade" && !args.includes("--dry-run")
      ? { code: 1, stderr: "Migration needs a retry", stdout: "" } : tag(args));
    await expect(installUpdate(api, "0.2.0")).rejects.toThrow();
    expect(api.installAppUpdate).not.toHaveBeenCalled();
  });
  it("does not restart the desktop after verification failure or a channel race", async () => {
    for (const code of [0, 1]) {
      const { api } = fixture();
      const tag = api.tag;
      api.tag = vi.fn(async (args) => args[0] === "version"
        ? { code, stderr: "failed", stdout: JSON.stringify({ version: "0.4.0" }) } : tag(args));
      await expect(installUpdate(api, "0.2.0")).rejects.toThrow("changed during");
      expect(api.installAppUpdate).not.toHaveBeenCalled();
    }
  });
});

