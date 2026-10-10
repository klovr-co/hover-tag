// Release channel: preview what switching does, and change nothing until confirmed.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { demoBridge, type RunResult } from "../lib/bridge";
import type { ProductUpdate } from "../lib/updates";
import type { Telemetry, TelemetryStatus } from "../lib/telemetry";
import { MemoryRow, ReleaseChannel, UsageDataRow } from "./Settings";

afterEach(cleanup);

const following: ProductUpdate = { version: "0.3.0", current: "0.3.0", runtime: false, desktop: false, channel: "stable", pinned: false, ahead: false };

function channels(newest: Record<string, string>, update: ProductUpdate | null = following) {
  const api = demoBridge();
  const calls: string[][] = [];
  const json = (value: unknown): RunResult => ({ code: 0, stdout: JSON.stringify(value), stderr: "" });
  let installed = "0.3.0";
  let saved = "stable";
  api.tag = async (args) => {
    calls.push(args);
    if (args[0] === "version") return json({ version: installed });
    const channel = args.includes("--channel") ? args[args.indexOf("--channel") + 1] : saved;
    const target = newest[channel];
    const order = (v: string) => v.replace(/-(alpha|beta)\.(\d+)/, (_, kind, n) => `.${kind === "alpha" ? 0 : 1}.${n}`);
    const status = target === installed ? "current" : order(target) < order(installed) ? "ahead" : "available";
    if (!args.includes("--dry-run")) { installed = status === "available" ? target : installed; saved = channel; }
    return json({ ok: true, status: args.includes("--dry-run") ? status : "upgraded",
      current: { version: installed, channel: saved, selection: "channel" }, target: { version: target, channel } });
  };
  api.checkAppUpdate = async (channel) => ({ version: newest[channel ?? "stable"] });
  api.installAppUpdate = vi.fn(async () => {});
  const switched = vi.fn();
  render(<ReleaseChannel api={api} appVersion="0.3.0" update={update} busy={false} setBusy={() => {}}
    switched={switched} setError={() => {}} />);
  return { calls, switched };
}

describe("Release channel", () => {
  it("can preview and confirm another channel after the initial update check failed", async () => {
    const { calls, switched } = channels({ stable: "0.3.0", beta: "0.4.0-beta.1", alpha: "0.4.0-alpha.1" }, null);
    const stable = screen.getByRole("radio", { name: "Stable" });
    expect(stable.hasAttribute("disabled")).toBe(false);
    expect(stable.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(stable);
    await screen.findByText("Switch to Stable. You already have its newest release.");
    expect(calls.every((args) => args.includes("--dry-run"))).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Switch" }));
    await vi.waitFor(() => expect(switched).toHaveBeenCalled());
    expect(calls).toContainEqual(["upgrade", "--channel", "stable", "--json"]);
  });

  it("previews a newer channel without changing anything, then switches and updates on confirm", async () => {
    const { calls, switched } = channels({ stable: "0.3.0", beta: "0.4.0-beta.1", alpha: "0.4.0-alpha.1" });
    fireEvent.click(screen.getByRole("radio", { name: "Beta" }));
    expect(await screen.findByText("Switch to Beta and update to Tag 0.4.0-beta.1. The app and your Tags update together, and your settings are kept.")).toBeTruthy();
    expect(calls.every((args) => args[0] !== "upgrade" || args.includes("--dry-run"))).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Switch and update" }));
    await vi.waitFor(() => expect(switched).toHaveBeenCalled());
    expect(calls).toContainEqual(["upgrade", "--channel", "beta", "--json"]);
  });

  it("says Tag keeps its version when the channel is behind", async () => {
    channels({ stable: "0.3.0", beta: "0.2.0-beta.4", alpha: "0.4.0-alpha.1" });
    fireEvent.click(screen.getByRole("radio", { name: "Beta" }));
    expect(await screen.findByText("Beta's newest release is older than Tag 0.3.0. Tag keeps 0.3.0 and follows Beta from its next release.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Switch" })).toBeTruthy();
  });

  it("Cancel leaves the current channel", async () => {
    const { calls } = channels({ stable: "0.3.0", beta: "0.4.0-beta.1", alpha: "0.4.0-alpha.1" });
    fireEvent.click(screen.getByRole("radio", { name: "Alpha" }));
    await screen.findByRole("button", { name: "Switch and update" });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("radio", { name: "Stable" }).getAttribute("aria-checked")).toBe("true");
    expect(calls.filter((args) => args[0] === "upgrade" && !args.includes("--dry-run"))).toEqual([]);
  });
});

describe("Usage data", () => {
  const status = (overrides: Partial<TelemetryStatus> = {}): TelemetryStatus => ({
    enabled: true, available: true, saved_preference: "on", process_override: null, privacy_notice: "https://example.invalid/privacy", ...overrides,
  });
  const telemetry = (value: TelemetryStatus | null): Telemetry => ({
    status: value, loaded: true, announced: false, recording: false, choose: vi.fn(async () => {}), seen: vi.fn(), acknowledge: vi.fn(), reload: vi.fn(), track: vi.fn(),
  });

  it("turns the installation-wide choice off and on, and links the privacy notice", async () => {
    const api = demoBridge();
    api.open = vi.fn(async () => {});
    const current = telemetry(status());
    render(<UsageDataRow api={api} telemetry={current} setError={() => {}} />);
    fireEvent.click(screen.getByRole("switch", { name: "Share usage data" }));
    await vi.waitFor(() => expect(current.choose).toHaveBeenCalledWith(false));
    fireEvent.click(screen.getByRole("button", { name: "Privacy notice" }));
    expect(api.open).toHaveBeenCalledWith("https://example.invalid/privacy");
  });

  it("explains why it can't be changed instead of offering a switch", () => {
    const api = demoBridge();
    const { rerender } = render(<UsageDataRow api={api} telemetry={telemetry(status({ enabled: false, process_override: "off" }))} setError={() => {}} />);
    expect(screen.getByText("Off for this app because TAG_TELEMETRY=off is set.")).toBeTruthy();
    expect(screen.queryByRole("switch")).toBeNull();
    rerender(<UsageDataRow api={api} telemetry={telemetry(status({ enabled: false, available: false, privacy_notice: null }))} setError={() => {}} />);
    expect(screen.getByText("This build of Tag doesn't collect usage data.")).toBeTruthy();
    rerender(<UsageDataRow api={api} telemetry={telemetry(null)} setError={() => {}} />);
    expect(screen.getByText("Update Tag to manage usage data here.")).toBeTruthy();
    expect(screen.queryByRole("switch")).toBeNull();
  });
});

describe("Memory row", () => {
  function memory(initial: { healthy: boolean; keep_running?: boolean }, stopFails = "") {
    const api = demoBridge();
    const calls: string[][] = [];
    let healthy = initial.healthy;
    api.tag = async (args) => {
      calls.push(args);
      if (args[1] === "start") healthy = true;
      if (args[1] === "stop") {
        if (stopFails) return { code: 1, stdout: "", stderr: stopFails };
        healthy = false;
      }
      return { code: 0, stderr: "", stdout: JSON.stringify({ schema_version: 1, managed: healthy, healthy, running_tags: [],
        keep_running: !!initial.keep_running, embedding: { provider: "openai", model: "m", local: false } }) };
    };
    const setError = vi.fn();
    render(<MemoryRow api={api} setError={setError} />);
    return { calls, setError };
  }

  it("shows whether memory runs and where its search model is, and starts it", async () => {
    const { calls } = memory({ healthy: false });
    await screen.findByText(/Not running\..*Search model is hosted \(openai\)\./);
    const toggle = screen.getByRole("switch", { name: "Memory" });
    expect(toggle.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(toggle);
    await vi.waitFor(() => expect(screen.getByRole("switch", { name: "Memory" }).getAttribute("aria-checked")).toBe("true"));
    expect(calls).toContainEqual(["memory", "start", "--json"]);
  });

  it("stops memory, and reports why when running Tags prevent it", async () => {
    const { calls, setError } = memory({ healthy: true }, "Stop every Tag bridge before stopping shared memory: research");
    const toggle = await screen.findByRole("switch", { name: "Memory" });
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(toggle);
    await vi.waitFor(() => expect(setError).toHaveBeenCalledWith("Stop every Tag bridge before stopping shared memory: research"));
    expect(calls).toContainEqual(["memory", "stop", "--json"]);
    expect(screen.getByRole("switch", { name: "Memory" }).getAttribute("aria-checked")).toBe("true");
  });
});

describe("Search model row", () => {
  function model(embedding: { provider: string; local: boolean; key_saved?: boolean }, outcome: object = { ok: true, reindexing: true }) {
    const api = demoBridge();
    let current = embedding;
    const sent: unknown[] = [];
    const calls: string[][] = [];
    api.tag = async (args) => {
      calls.push(args);
      if (args[2] === "local") current = { provider: "onnx", local: true, key_saved: true };
      return { code: 0, stderr: "", stdout: JSON.stringify({ schema_version: 1, managed: true, healthy: true, running_tags: [],
        keep_running: true, embedding: { model: "m", ...current } }) };
    };
    api.setup = async (args, onLine, onExit) => {
      calls.push(args);
      if ((outcome as { ok: boolean }).ok) current = { provider: "openai", local: false, key_saved: true };
      setTimeout(() => { onLine(JSON.stringify(outcome)); onExit(0, ""); }, 0);
      return { send: (m) => sent.push(m), stop: () => {} };
    };
    const setError = vi.fn();
    render(<MemoryRow api={api} setError={setError} />);
    return { calls, sent, setError };
  }

  it("shows where the search model runs and asks for a key before switching to OpenAI", async () => {
    const { sent, calls } = model({ provider: "onnx", local: true });
    const local = await screen.findByRole("radio", { name: "On this computer" });
    expect(local.getAttribute("aria-checked")).toBe("true");
    expect(screen.queryByLabelText("OpenAI API key")).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: "OpenAI" }));
    const save = screen.getByRole("button", { name: "Save and switch" });
    expect(save.hasAttribute("disabled")).toBe(true);
    fireEvent.change(screen.getByLabelText("OpenAI API key"), { target: { value: "sk-synthetic" } });
    expect(save.hasAttribute("disabled")).toBe(false);
    expect(screen.getByText(/indexes your Slack channels again/)).toBeTruthy();
    fireEvent.click(save);
    await vi.waitFor(() => expect(sent).toEqual([{ api_key: "sk-synthetic" }]));
    await vi.waitFor(() => expect(screen.getByRole("radio", { name: "OpenAI" }).getAttribute("aria-checked")).toBe("true"));
    expect(calls.flat()).not.toContain("sk-synthetic");
    expect(screen.queryByLabelText("OpenAI API key")).toBeNull();
  });

  it("doesn't need the key again when one is saved, and switches back to local", async () => {
    const { calls } = model({ provider: "openai", local: false, key_saved: true });
    fireEvent.click(await screen.findByRole("radio", { name: "On this computer" }));
    fireEvent.click(screen.getByRole("button", { name: "Switch" }));
    await vi.waitFor(() => expect(calls).toContainEqual(["memory", "embedding", "local", "--json"]));
  });

  it("keeps the old choice and says why when OpenAI refuses the key", async () => {
    const { setError } = model({ provider: "onnx", local: true }, { ok: false, error: "OpenAI rejected that key." });
    fireEvent.click(await screen.findByRole("radio", { name: "OpenAI" }));
    fireEvent.change(screen.getByLabelText("OpenAI API key"), { target: { value: "sk-wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "Save and switch" }));
    await vi.waitFor(() => expect(setError).toHaveBeenCalledWith("OpenAI rejected that key."));
    expect(screen.getByRole("radio", { name: "On this computer" }).getAttribute("aria-checked")).toBe("true");
  });
});
