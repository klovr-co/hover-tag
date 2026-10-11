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

it("names the built-in search model and says it downloads in the background without blocking search-free use", () => {
  render(<Welcome api={demoBridge()} platform="macos" install={vi.fn()} />);
  expect(screen.getByText(/Memory search uses a built-in model \(bge-m3, 560 MB\)/)).toBeTruthy();
  expect(screen.getByText(/downloads in the background after install/)).toBeTruthy();
  expect(screen.getByText(/Search isn't available until it finishes/)).toBeTruthy();
  fireEvent.click(screen.getByRole("switch", { name: "Use my own search model" }));
  // With their own model there is nothing to download, so the note goes away.
  expect(screen.queryByText(/downloads in the background after install/)).toBeNull();
});

it("lets people skip the built-in search model and name their own", () => {
  const install = vi.fn();
  render(<Welcome api={demoBridge()} platform="macos" install={install} />);
  const toggle = screen.getByRole("switch", { name: "Use my own search model" });
  expect(toggle.getAttribute("aria-checked")).toBe("false");
  expect(screen.queryByLabelText("Endpoint address")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Install Tag" }));
  expect(install).toHaveBeenLastCalledWith({ skipModel: false, baseUrl: "", model: "", apiKey: "" });
  fireEvent.click(toggle);
  expect(toggle.getAttribute("aria-checked")).toBe("true");
  fireEvent.change(screen.getByLabelText("Endpoint address"), { target: { value: " http://localhost:11434/v1 " } });
  fireEvent.change(screen.getByLabelText("Model name"), { target: { value: "nomic-embed-text" } });
  fireEvent.click(screen.getByRole("button", { name: "Install Tag" }));
  expect(install).toHaveBeenLastCalledWith({ skipModel: true, baseUrl: "http://localhost:11434/v1", model: "nomic-embed-text", apiKey: "" });
  fireEvent.change(screen.getByLabelText("API key (optional)"), { target: { value: " sk-synthetic " } });
  fireEvent.click(screen.getByRole("button", { name: "Install Tag" }));
  expect(install).toHaveBeenLastCalledWith({ skipModel: true, baseUrl: "http://localhost:11434/v1", model: "nomic-embed-text", apiKey: "sk-synthetic" });
});

it("tells the installer to skip the built-in model download", async () => {
  const api = demoBridge();
  const install = vi.spyOn(api, "install");
  render(<Installing api={api} skipModel done={vi.fn()} retry={vi.fn()} cancel={vi.fn()} />);
  await vi.waitFor(() => expect(install).toHaveBeenCalled());
  expect(install.mock.calls[0][3]).toEqual({ skipMemoryModel: true });
});
