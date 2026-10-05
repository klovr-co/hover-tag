import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
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

it("shows a broken launcher error on first open and recovers after retry", async () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  const api = demoBridge();
  const working = api.tag;
  let repaired = false;
  api.tag = vi.fn(async (args: string[]) => args[0] === "list" && !repaired
    ? { code: 126, stdout: "", stderr: "tag: missing-python: No such file or directory\n" }
    : working(args));
  vi.mocked(bridge).mockResolvedValue(api);
  render(<App />);
  expect(await screen.findByText("Couldn't open Tag")).toBeTruthy();
  expect(screen.getByText(/missing-python: No such file or directory/)).toBeTruthy();
  repaired = true;
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByRole("heading", { name: "Your Tags" })).toBeTruthy();
  expect(screen.queryByText(/missing-python/)).toBeNull();
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
  fireEvent.click(screen.getByRole("button", { name: "Happy to help" }));
  await vi.waitFor(() => expect(records().map((args) => args[2])).toEqual(expect.arrayContaining(["app_opened", "app_screen_viewed"])));
  expect(api.tag).toHaveBeenCalledWith(["telemetry", "on", "--json"]);
  expect(screen.queryByText("Help support Tag's development")).toBeNull();
  expect(records().filter((args) => args[2] === "app_opened")).toHaveLength(1);
});


it("replays onboarding from Settings without saving, installing, or quitting", async () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  const api = demoBridge();
  api.tag = vi.fn(api.tag);
  api.quit = vi.fn(api.quit);
  api.install = vi.fn(api.install);
  vi.mocked(bridge).mockResolvedValue(api);
  render(<StrictMode><App /></StrictMode>);
  fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
  fireEvent.click(await screen.findByRole("tab", { name: "About" }));
  fireEvent.click(await screen.findByRole("button", { name: "Replay" }));
  fireEvent.click(await screen.findByRole("button", { name: "No thanks" }));
  expect(await screen.findByRole("button", { name: "Done" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Install Tag" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Quit" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Done" }));
  expect(await screen.findByRole("button", { name: "Replay" })).toBeTruthy();
  expect(api.tag).not.toHaveBeenCalledWith(["telemetry", "off", "--json"]);
  expect(api.quit).not.toHaveBeenCalled();
  expect(api.install).not.toHaveBeenCalled();
});

it("keeps measuring Home after it replaces the startup element", async () => {
  const observed = new Map<Element, ResizeObserverCallback>();
  vi.stubGlobal("ResizeObserver", class {
    element?: Element;
    constructor(private callback: ResizeObserverCallback) {}
    observe(element: Element) { this.element = element; observed.set(element, this.callback); }
    disconnect() { if (this.element) observed.delete(this.element); }
  });
  const api = demoBridge();
  api.fitWindow = vi.fn(async () => {});
  vi.mocked(bridge).mockResolvedValue(api);
  const { container } = render(<StrictMode><App /></StrictMode>);
  const startup = container.querySelector("main")!;
  await screen.findByRole("heading", { name: "Your Tags" });
  const home = container.querySelector("main")!;
  expect(home).not.toBe(startup);
  expect([...observed.keys()]).toEqual([home]);
  vi.spyOn(home, "getBoundingClientRect").mockReturnValue({ height: 720 } as DOMRect);
  act(() => observed.get(home)!([{ target: home, contentRect: home.getBoundingClientRect(),
    borderBoxSize: [], contentBoxSize: [], devicePixelContentBoxSize: [] }], {} as ResizeObserver));
  expect(api.fitWindow).toHaveBeenLastCalledWith(520, 720);
});

it("reports a missing feed as a check failure after retry and opens channel settings", async () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  const api = demoBridge();
  const tag = api.tag;
  api.tag = vi.fn(async (args: string[]) => args[0] === "upgrade"
    ? { code: 0, stderr: "", stdout: JSON.stringify({ ok: true, status: "current", current: { version: "0.2.0", channel: "stable" } }) }
    : tag(args));
  api.checkAppUpdate = vi.fn(async () => { throw new Error("Stable feed unavailable"); });
  vi.mocked(bridge).mockResolvedValue(api);
  render(<App />);
  await screen.findByText("Couldn't check for updates");
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await screen.findByText("Couldn't check for updates");
  expect(screen.queryByText("The update didn't finish")).toBeNull();
  expect(vi.mocked(api.tag).mock.calls.filter(([args]) => args[0] === "upgrade").every(([args]) => args.includes("--dry-run"))).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Release channel settings" }));
  expect(await screen.findByRole("radio", { name: /Beta/ })).toBeTruthy();
});
