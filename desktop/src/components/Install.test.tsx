import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { demoBridge, type Session } from "../lib/bridge";
import { Installing } from "./Install";

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
