import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { demoBridge, type Session } from "../lib/bridge";
import { Installing, Welcome } from "./Install";

afterEach(cleanup);

it("starts the installer once under StrictMode and stops a late session", async () => {
  const api = demoBridge();
  let finish!: (session: Session) => void;
  api.install = vi.fn(() => new Promise<Session>((resolve) => { finish = resolve; }));
  const view = render(<StrictMode><Installing api={api} done={vi.fn()} retry={vi.fn()} cancel={vi.fn()} /></StrictMode>);
  await vi.waitFor(() => expect(api.install).toHaveBeenCalledTimes(1));
  view.unmount();
  const stop = vi.fn();
  await act(async () => finish({ send: vi.fn(), stop }));
  expect(stop).toHaveBeenCalledTimes(1);
});

it("turns installer launch failures into a retryable error", async () => {
  const api = demoBridge();
  api.install = vi.fn().mockRejectedValue(new Error("Installer unavailable"));
  const retry = vi.fn();
  render(<Installing api={api} done={vi.fn()} retry={retry} cancel={vi.fn()} />);
  expect(await screen.findByText("Error: Installer unavailable")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(retry).toHaveBeenCalledTimes(1);
});

it("keeps one canvas: details replace the checklist instead of growing the window", async () => {
  const api = demoBridge();
  api.install = vi.fn(() => new Promise<Session>(() => {}));
  const { container } = render(<Installing api={api} done={vi.fn()} retry={vi.fn()} cancel={vi.fn()} />);
  expect(container.querySelector(".body.install")).toBeTruthy();
  expect(screen.getByText("Getting installation tools")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Show details" }));
  expect(screen.getByLabelText("Installer log")).toBeTruthy();
  expect(screen.queryByText("Getting installation tools")).toBeNull();
});

it("draws Welcome on the same canvas as installation", () => {
  const { container } = render(<Welcome api={demoBridge()} platform="macos" install={vi.fn()} />);
  expect(container.querySelector(".sky.hero")).toBeTruthy();
  expect(container.querySelector(".body.install")).toBeTruthy();
});

it("tells people the install needs about 1.8 GB, as measured", () => {
  render(<Welcome api={demoBridge()} platform="macos" install={vi.fn()} />);
  expect(screen.getByText("1.8 GB")).toBeTruthy();
  expect(screen.queryByText("650 MB")).toBeNull();
});

const toModel = () => fireEvent.click(screen.getByRole("button", { name: "Continue" }));

it("keeps the first screen to what installing means, and asks about search on a second one", () => {
  render(<Welcome api={demoBridge()} platform="macos" install={vi.fn()} />);
  expect(screen.getByText("Install Tag")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Install Tag" })).toBeNull();
  expect(screen.queryByRole("switch")).toBeNull();
  toModel();
  expect(screen.getByText("Memory search")).toBeTruthy();
  expect(screen.getByText("How should memory search?")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Install Tag" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(screen.getByRole("button", { name: "Continue" })).toBeTruthy();
});

it("names the built-in search model and says it downloads in the background and search waits for it", () => {
  render(<Welcome api={demoBridge()} platform="macos" install={vi.fn()} />);
  toModel();
  expect(screen.getByRole("radio", { name: "Built in" }).getAttribute("aria-checked")).toBe("true");
  expect(screen.getByText(/bge-m3 runs on this computer/)).toBeTruthy();
  expect(screen.getByText(/560 MB downloads after install; search waits for it/)).toBeTruthy();
});

it("lets people skip the built-in search model and name their own", () => {
  const install = vi.fn();
  render(<Welcome api={demoBridge()} platform="macos" install={install} />);
  toModel();
  expect(screen.queryByLabelText("Endpoint address")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Install Tag" }));
  expect(install).toHaveBeenLastCalledWith({ skipModel: false, baseUrl: "", model: "", apiKey: "" });
  fireEvent.click(screen.getByRole("radio", { name: "Your own endpoint" }));
  expect(screen.getByRole("radio", { name: "Your own endpoint" }).getAttribute("aria-checked")).toBe("true");
  fireEvent.change(screen.getByLabelText("Endpoint address"), { target: { value: " http://localhost:11434/v1 " } });
  fireEvent.change(screen.getByLabelText("Model name"), { target: { value: "nomic-embed-text" } });
  fireEvent.click(screen.getByRole("button", { name: "Install Tag" }));
  expect(install).toHaveBeenLastCalledWith({ skipModel: true, baseUrl: "http://localhost:11434/v1", model: "nomic-embed-text", apiKey: "" });
  fireEvent.change(screen.getByLabelText("API key (optional)"), { target: { value: " sk-synthetic " } });
  fireEvent.click(screen.getByRole("button", { name: "Install Tag" }));
  expect(install).toHaveBeenLastCalledWith({ skipModel: true, baseUrl: "http://localhost:11434/v1", model: "nomic-embed-text", apiKey: "sk-synthetic" });
});

it("keeps what was typed when going back and forward, and the replay has no search screen", () => {
  render(<Welcome api={demoBridge()} platform="macos" install={vi.fn()} />);
  toModel();
  fireEvent.click(screen.getByRole("radio", { name: "Your own endpoint" }));
  fireEvent.change(screen.getByLabelText("Model name"), { target: { value: "bge-m3" } });
  fireEvent.click(screen.getByRole("button", { name: "Back" }));
  toModel();
  expect((screen.getByLabelText("Model name") as HTMLInputElement).value).toBe("bge-m3");
  cleanup();
  render(<Welcome api={demoBridge()} platform="macos" preview={vi.fn()} />);
  expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
});

it("tells the installer to skip the built-in model download", async () => {
  const api = demoBridge();
  const install = vi.spyOn(api, "install");
  render(<Installing api={api} skipModel done={vi.fn()} retry={vi.fn()} cancel={vi.fn()} />);
  await vi.waitFor(() => expect(install).toHaveBeenCalled());
  expect(install.mock.calls[0][3]).toEqual({ skipMemoryModel: true });
});
