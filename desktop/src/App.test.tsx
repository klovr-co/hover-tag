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
