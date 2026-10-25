import { describe, expect, it, vi } from "vitest";
import { demoBridge } from "./bridge";
import { checkUpdate, initialUpdate, installUpdate, updateReducer } from "./updates";

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
  it.each([null, "0.4.0"])("waits for desktop artifacts before offering a release (%s)", async (app) => {
    const { api, events } = fixture("0.2.0", "0.3.0", app);
    expect(await checkUpdate(api, "0.2.0")).toMatchObject({ version: "0.2.0", runtime: false, desktop: false, preparing: "0.3.0" });
    expect(await installUpdate(api, "0.2.0")).toMatchObject({ current: "0.2.0", preparing: "0.3.0" });
    expect(events).toEqual([]);
  });
  it("refuses a channel switch onto a release the app can't follow yet", async () => {
    const { api, events } = fixture("0.2.0", "0.3.0", null);
    await expect(installUpdate(api, "0.2.0", "beta")).rejects.toThrow("still being prepared");
    expect(events).toEqual([]);
  });
  it("still reports a mismatch when the installed app and Tags differ", async () => {
    const { api } = fixture("0.2.0", "0.3.0", null);
    await expect(checkUpdate(api, "0.1.0")).rejects.toThrow("complete Tag update");
  });
  it("keeps both versions installed when the desktop feed cannot be fetched", async () => {
    const { api, events } = fixture();
    vi.mocked(api.checkAppUpdate).mockRejectedValue(new Error("Tag.app's alpha update feed is unavailable."));
    await expect(installUpdate(api, "0.2.0")).rejects.toThrow("alpha update feed is unavailable");
    expect(events).toEqual([]);
    expect(api.tag).toHaveBeenCalledTimes(1);
    expect(api.tag).toHaveBeenCalledWith(["upgrade", "--dry-run", "--json"]);
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

// A runtime that follows a saved channel, like `tag upgrade --channel`.
function channels(installed: string, saved: string, newest: Record<string, string>) {
  const api = demoBridge();
  const events: string[] = [];
  const rank = (v: string) => v.split(/[.-]/).map((part) => Number(part) || 0);
  const older = (a: string, b: string) => rank(a) < rank(b) || (a.includes("-") && !b.includes("-") && a.split("-")[0] === b);
  api.checkAppUpdate = vi.fn(async (channel?: string) => ({ version: newest[channel ?? "stable"] }));
  api.installAppUpdate = vi.fn(async (version: string) => { events.push(`desktop ${version}`); });
  api.tag = vi.fn(async (args: string[]) => {
    if (args[0] === "version") return { code: 0, stderr: "", stdout: JSON.stringify({ version: installed }) };
    const channel = args.includes("--channel") ? args[args.indexOf("--channel") + 1] : saved;
    const target = newest[channel];
    const dry = args.includes("--dry-run");
    const before = installed;
    let status = target === installed ? "current" : older(target, installed) ? "ahead" : "available";
    if (!dry) {
      if (status === "available") { installed = target; status = "upgraded"; events.push(`runtime ${target}`); }
      else if (channel !== saved) status = status === "ahead" ? "channel-updated" : "policy-updated";
      saved = channel;
    }
    return { code: 0, stderr: "", stdout: JSON.stringify({ ok: true, status,
      current: { version: before, channel: saved, selection: "channel" }, target: { version: target, channel } }) };
  });
  return { api, events, saved: () => saved };
}

describe("release channels", () => {
  const newest = { stable: "0.3.0", beta: "0.4.0-beta.1", alpha: "0.4.0-alpha.3" };

  it("checks Tag.app's feed for the channel the runtime follows", async () => {
    const { api } = channels("0.3.0", "beta", newest);
    expect(await checkUpdate(api, "0.3.0")).toMatchObject({ version: "0.4.0-beta.1", channel: "beta", desktop: true });
    expect(api.checkAppUpdate).toHaveBeenCalledWith("beta");
  });

  it("previews a switch without saving it", async () => {
    const { api, events, saved } = channels("0.3.0", "stable", newest);
    expect(await checkUpdate(api, "0.3.0", "alpha")).toMatchObject({ version: "0.4.0-alpha.3", channel: "alpha", runtime: true });
    expect(saved()).toBe("stable");
    expect(events).toEqual([]);
  });

  it("switches channel and updates the app and runtime to the same release", async () => {
    const { api, events, saved } = channels("0.3.0", "stable", newest);
    expect(await installUpdate(api, "0.3.0", "beta")).toMatchObject({ current: "0.4.0-beta.1", channel: "beta" });
    expect(api.tag).toHaveBeenCalledWith(["upgrade", "--channel", "beta", "--json"]);
    expect(events).toEqual(["runtime 0.4.0-beta.1", "desktop 0.4.0-beta.1"]);
    expect(saved()).toBe("beta");
  });

  it("saves a switch to an older channel and keeps the newer release until it catches up", async () => {
    const { api, events, saved } = channels("0.4.0-alpha.3", "alpha", newest);
    expect(await checkUpdate(api, "0.4.0-alpha.3", "stable")).toMatchObject({ ahead: true, runtime: false, desktop: false });
    await installUpdate(api, "0.4.0-alpha.3", "stable");
    expect(events).toEqual([]);
    expect(saved()).toBe("stable");
  });

  it("explains that Tag.app can't follow edge", async () => {
    const { api } = channels("0.3.0", "edge", { ...newest, edge: "0.4.0-alpha.4" });
    await expect(checkUpdate(api, "0.3.0")).rejects.toThrow("doesn't follow the edge channel");
    expect(api.checkAppUpdate).not.toHaveBeenCalled();
  });

  it("follows no channel while pinned until one is chosen", async () => {
    const { api } = channels("0.3.0", "stable", newest);
    const tag = api.tag;
    let pinned = true;
    api.tag = vi.fn(async (args) => {
      if (args.includes("--channel") && !args.includes("--dry-run")) pinned = false;
      return !pinned || args.includes("--channel") || args[0] === "version" ? tag(args)
        : { code: 0, stderr: "", stdout: JSON.stringify({ ok: true, status: "pinned", current: { version: "0.3.0", channel: "stable", selection: "version" } }) };
    });
    expect(await checkUpdate(api, "0.3.0")).toMatchObject({ pinned: true, channel: null });
    expect(await installUpdate(api, "0.3.0", "beta")).toMatchObject({ pinned: false, channel: "beta" });
  });
});

describe("update state", () => {
  it("never reports a failed check as an unfinished update, even when two checks overlap", async () => {
    const { updateReducer, initialUpdate } = await import("./updates");
    let state = updateReducer(initialUpdate, { type: "checking" });
    state = updateReducer(state, { type: "checking" });
    state = updateReducer(state, { type: "checkFailed", error: "Tag is not managed by the installer." });
    state = updateReducer(state, { type: "checkFailed", error: "Tag is not managed by the installer." });
    expect(state).toMatchObject({ status: "idle", error: "Tag is not managed by the installer." });
    expect(updateReducer({ ...state, status: "updating" }, { type: "failed", error: "Feed unavailable" }).status).toBe("idle");
    for (const phase of ["runtime", "app"] as const) {
      expect(updateReducer({ ...state, status: "updating", phase }, { type: "failed", error: "Download failed" }).status).toBe("failed");
    }
  });

  it("says a JSON command's error, not its JSON", async () => {
    const { failureLine } = await import("./tags");
    const stdout = '{"schema_version": 1, "ok": false, "error": "Tag is not managed by the installer. Install it once before using tag upgrade."}';
    expect(failureLine({ code: 1, stdout, stderr: "" }, "x")).toBe("Tag is not managed by the installer. Install it once before using tag upgrade.");
    expect(failureLine({ code: 1, stdout: "", stderr: "tag_cli.py: error: bad\n" }, "x")).toBe("bad");
  });
});

describe("app release channel migration v1", () => {
  const newest = { stable: "0.2.0", beta: "0.3.0-beta.1", alpha: "0.3.0-alpha.2" };
  function unmigrated(installed = "0.2.0", saved = "stable") {
    const fixture = channels(installed, saved, newest);
    const info = fixture.api.info;
    let initialized = false;
    fixture.api.info = async () => ({ ...await info(), channelInitialized: initialized });
    fixture.api.markChannelInitialized = vi.fn(async () => { initialized = true; });
    return { ...fixture, initialized: () => initialized };
  }

  it("completes without an update notice when the saved channel already matches the app", async () => {
    const { api, saved, initialized } = unmigrated(newest.beta, "beta");
    const update = await checkUpdate(api, newest.beta);
    expect(update.initializeChannel).toBeUndefined();
    expect(updateReducer(initialUpdate, { type: "checked", update }).status).toBe("current");
    expect(saved()).toBe("beta");
    expect(initialized()).toBe(true);
  });

  it.each(["beta", "alpha"] as const)("defaults to the published %s app channel without mutating a preview", async (channel) => {
    const { api, saved, initialized } = unmigrated();
    expect(await checkUpdate(api, newest[channel])).toMatchObject({ channel, initializeChannel: true });
    expect(saved()).toBe("stable");
    expect(initialized()).toBe(false);
    expect(vi.mocked(api.tag).mock.calls.every(([args]) => args.includes("--dry-run"))).toBe(true);
  });

  it("migrates an older Stable runtime to Beta and preserves a later explicit choice across checks", async () => {
    const { api, saved, initialized } = unmigrated();
    await installUpdate(api, newest.beta);
    expect(saved()).toBe("beta");
    expect(initialized()).toBe(true);
    expect(api.checkAppUpdate).not.toHaveBeenCalled(); // The installed app already matches.
    await installUpdate(api, newest.beta, "stable");
    expect(await checkUpdate(api, newest.beta)).toMatchObject({ channel: "stable", ahead: true });
    expect(saved()).toBe("stable");
  });

  it("saves the default even when app and runtime versions already match", async () => {
    const { api, saved } = unmigrated(newest.beta);
    await installUpdate(api, newest.beta);
    expect(saved()).toBe("beta");
    expect(api.tag).toHaveBeenCalledWith(["upgrade", "--channel", "beta", "--json"]);
    vi.mocked(api.tag).mockClear();
    await installUpdate(api, newest.beta);
    expect(vi.mocked(api.tag).mock.calls.filter(([args]) => args[0] === "upgrade" && !args.includes("--dry-run"))).toEqual([]);
  });

  it("retries a failed upgrade without prematurely completing the migration", async () => {
    const { api, initialized } = unmigrated();
    const tag = api.tag;
    let fail = true;
    api.tag = vi.fn(async (args) => fail && args[0] === "upgrade" && !args.includes("--dry-run")
      ? { code: 1, stdout: "", stderr: "Interrupted" } : tag(args));
    await expect(installUpdate(api, newest.beta)).rejects.toThrow("Interrupted");
    expect(initialized()).toBe(false);
    fail = false;
    await installUpdate(api, newest.beta);
    expect(initialized()).toBe(true);
  });

  it("does not complete migration when the saved policy cannot be verified", async () => {
    const { api, initialized } = unmigrated();
    const tag = api.tag;
    let writes = 0;
    api.tag = vi.fn(async (args) => {
      if (args[0] === "upgrade" && !args.includes("--dry-run")) writes++;
      if (writes && args[0] === "upgrade" && !args.includes("--channel")) {
        return { code: 0, stderr: "", stdout: JSON.stringify({ ok: true, current: { channel: "stable", selection: "channel" } }) };
      }
      return tag(args);
    });
    await expect(installUpdate(api, newest.beta)).rejects.toThrow("verify the saved release channel");
    expect(initialized()).toBe(false);
  });

  it("resumes safely if recording migration completion fails", async () => {
    const { api, initialized, saved } = unmigrated();
    vi.mocked(api.markChannelInitialized).mockRejectedValueOnce(new Error("Disk full"));
    await expect(installUpdate(api, newest.beta)).rejects.toThrow("Disk full");
    expect(initialized()).toBe(false);
    expect(saved()).toBe("beta");
    await installUpdate(api, newest.beta);
    expect(initialized()).toBe(true);
  });

  it("preserves an exact pin on an older installation", async () => {
    const { api } = unmigrated();
    api.tag = vi.fn(async () => ({ code: 0, stderr: "", stdout: JSON.stringify({ ok: true, status: "pinned",
      current: { version: newest.stable, selection: "version", channel: "stable" } }) }));
    expect(await checkUpdate(api, newest.stable)).toMatchObject({ pinned: true, channel: null });
    expect(vi.mocked(api.tag).mock.calls.every(([args]) => !args.includes("--channel"))).toBe(true);
  });
});
