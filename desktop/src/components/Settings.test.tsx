// Release channel: preview what switching does, and change nothing until confirmed.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { demoBridge, type RunResult } from "../lib/bridge";
import type { ProductUpdate } from "../lib/updates";
import type { Telemetry, TelemetryStatus } from "../lib/telemetry";
import { ReleaseChannel, UsageDataRow } from "./Settings";

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
    status: value, loaded: true, announced: false, recording: false, choose: vi.fn(async () => {}), acknowledge: vi.fn(), reload: vi.fn(), track: vi.fn(),
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
