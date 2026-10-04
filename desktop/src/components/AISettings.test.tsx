import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { demoBridge } from "../lib/bridge";
import type { Tags } from "../lib/tags";
import { AISettings } from "./AISettings";

afterEach(cleanup);

describe("Shared AI settings", () => {
  it("manages connections before any Tags exist, without a Tag or model selector", async () => {
    const api = demoBridge();
    const run = vi.spyOn(api, "tag");
    const tags = { rows: [], refresh: vi.fn() } as unknown as Tags;
    render(<AISettings api={api} tags={tags} close={vi.fn()} />);
    expect(await screen.findByText("Shared by all your Tags")).toBeTruthy();
    await screen.findAllByRole("button", { name: "Change account" });
    expect(run).toHaveBeenCalledWith(["settings", "ai", "connections", "--json"]);
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByText("Default model")).toBeNull();
  });

  it("changes a provider through the global command and explains all running Tags pause", async () => {
    const api = demoBridge();
    const setup = vi.spyOn(api, "setup");
    const tags = { rows: [], refresh: vi.fn() } as unknown as Tags;
    render(<AISettings api={api} tags={tags} close={vi.fn()} />);
    fireEvent.click((await screen.findAllByRole("button", { name: "Change account" }))[0]);
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("Running Tags pause");
    const radio = screen.getByRole("radio", { name: /ChatGPT account/ });
    fireEvent.click(radio);
    fireEvent.click(screen.getByRole("button", { name: /Continue in browser/ }));
    await vi.waitFor(() => expect(setup).toHaveBeenCalled());
    expect(setup.mock.calls[0][0]).toEqual(["settings", "ai", "sign-in", "codex", "--method", "chatgpt", "--restart"]);
  });
});
