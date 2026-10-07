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
