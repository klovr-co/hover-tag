import { StrictMode } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "./App";
import { bridge, demoBridge, type AppInfo } from "./lib/bridge";

vi.mock("./lib/bridge", async (original) => ({ ...await original<typeof import("./lib/bridge")>(), bridge: vi.fn() }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

it("shows a styled startup and recovers when reading app information fails", async () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  const api = demoBridge({ installed: false });
  let reject!: (error: Error) => void;
  api.info = vi.fn().mockImplementationOnce(() => new Promise<AppInfo>((_resolve, fail) => { reject = fail; }))
    .mockRejectedValueOnce(new Error("Tag unavailable"))
    .mockResolvedValue({ platform: "macos", cli: null, version: "test" });
  vi.mocked(bridge).mockResolvedValue(api);
  render(<StrictMode><App /></StrictMode>);
  expect(screen.getByText("Welcome to Tag")).toBeTruthy();
  await vi.waitFor(() => expect(api.info).toHaveBeenCalledTimes(2));
  expect(await screen.findByText("Couldn't open Tag")).toBeTruthy();
  // The discarded mount must not overwrite the surviving mount's state.
  reject(new Error("Stale error"));
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByRole("button", { name: "Install Tag" })).toBeTruthy();
  expect(screen.queryByText("Couldn't open Tag")).toBeNull();
});

it("asks about usage data before Home, then records only after the choice", async () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  const api = demoBridge();
  const demo = api.tag;
  let saved = "not_set";
  api.tag = vi.fn(async (args: string[]) => {
    if (args[0] !== "telemetry" || args[1] === "record") return demo(args);
    if (args[1] === "on" || args[1] === "off") saved = args[1];
    return { code: 0, stderr: "", stdout: JSON.stringify({ schema_version: 1, enabled: saved === "on", available: true,
      saved_preference: saved, process_override: null, privacy_notice: "https://example.invalid/privacy" }) };
  });
  vi.mocked(bridge).mockResolvedValue(api);
  render(<StrictMode><App /></StrictMode>);
  expect(await screen.findByText("Help support Tag's development")).toBeTruthy();
  const records = () => vi.mocked(api.tag).mock.calls.map(([args]) => args).filter((args) => args[1] === "record");
  expect(records()).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  await vi.waitFor(() => expect(records().map((args) => args[2])).toEqual(expect.arrayContaining(["app_opened", "app_screen_viewed"])));
  expect(api.tag).toHaveBeenCalledWith(["telemetry", "on", "--json"]);
  expect(screen.queryByText("Help support Tag's development")).toBeNull();
  expect(records().filter((args) => args[2] === "app_opened")).toHaveLength(1);
});
