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

describe("Your own API", () => {
  const tags = () => ({ rows: [{ id: "t0klovr1-a0maya01", slack_name: "Maya's Tag", valid: true, state: "running" },
    { id: "t0ops", slack_name: "Ops Tag", valid: true, state: "stopped" }], refresh: vi.fn() }) as unknown as Tags;
  const reply = (args: string[], ok = (_: string) => true) => JSON.stringify(ok(args[0])
    ? { type: "api", action: args[4], backend: args[6], status: "saved", in_use: true, restarted: true }
    : { type: "api", action: args[4], backend: args[6], status: "failed", error: "Maya's Tag didn't start with the new connection, so the previous one was kept." });

  it("adds an API for every Tag at once, sending the key only over stdin", async () => {
    const api = demoBridge();
    const sent: unknown[] = [];
    let live = 0, most = 0;
    const setup = vi.spyOn(api, "setup").mockImplementation(async (args, onLine, onExit) => {
      most = Math.max(most, ++live);
      setTimeout(() => { live--; onLine(JSON.stringify({ type: "progress", text: "Saving the connection…" })); onLine(reply(args)); onExit(0, ""); }, 20);
      return { send: (message) => sent.push(message), stop: vi.fn() };
    });
    render(<AISettings api={api} tags={tags()} close={vi.fn()} apiConnections />);
    fireEvent.click(await screen.findByRole("button", { name: /Add your own API/ }));
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.getAllByRole("radio").map((r) => r.textContent)).toEqual(["Codex", "Claude"]);
    fireEvent.click(screen.getByRole("radio", { name: "Claude" }));
    fireEvent.change(screen.getByPlaceholderText("https://api.anthropic.com"), { target: { value: "https://gateway.example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Enter at least one model");
    fireEvent.change(screen.getByPlaceholderText("claude-sonnet-5-5"), { target: { value: "claude-sonnet-5-5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Paste the API key");
    const key = screen.getByPlaceholderText("Paste key") as HTMLInputElement;
    expect(key.type).toBe("password");
    fireEvent.change(key, { target: { value: "sk-ant-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(setup).toHaveBeenCalledTimes(2));
    expect(setup.mock.calls.map((c) => c[0][0])).toEqual(["t0klovr1-a0maya01", "t0ops"]);
    expect(setup.mock.calls[0][0]).toEqual(["t0klovr1-a0maya01", "settings", "ai", "api", "set", "--backend", "claude", "--kind", "anthropic",
      "--base-url", "https://gateway.example.com", "--models", "claude-sonnet-5-5", "--restart"]);
    expect(setup.mock.calls.flatMap((c) => c[0]).join(" ")).not.toContain("sk-ant-secret");
    await vi.waitFor(() => expect(sent).toEqual([{ api_key: "sk-ant-secret" }, { api_key: "sk-ant-secret" }]));
    expect(most).toBe(2);
    expect(await screen.findByText("Claude · API (gateway.example.com) saved.")).toBeTruthy();
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("detects Azure OpenAI from the URL and asks for its API version", async () => {
    const api = demoBridge();
    render(<AISettings api={api} tags={tags()} close={vi.fn()} apiConnections />);
    fireEvent.click(await screen.findByRole("button", { name: /Add your own API/ }));
    expect(screen.queryByText("Azure OpenAI")).toBeNull();
    fireEvent.change(screen.getByPlaceholderText("https://api.openai.com/v1"), { target: { value: "https://acme.openai.azure.com/openai" } });
    expect(screen.getByText("Azure OpenAI")).toBeTruthy();
    expect(screen.getByPlaceholderText("2025-04-01-preview")).toBeTruthy();
    expect(screen.getByText("Deployment names")).toBeTruthy();
  });

  it("retries only the Tags that failed", async () => {
    const api = demoBridge();
    const setup = vi.spyOn(api, "setup").mockImplementation(async (args, onLine, onExit) => {
      setTimeout(() => { onLine(reply(args, (tag) => tag === "t0ops")); onExit(0, ""); }, 10);
      return { send: vi.fn(), stop: vi.fn() };
    });
    render(<AISettings api={api} tags={tags()} close={vi.fn()} apiConnections />);
    fireEvent.click(await screen.findByRole("button", { name: /Add your own API/ }));
    fireEvent.change(screen.getByPlaceholderText("gpt-5.5"), { target: { value: "gpt-5.5" } });
    fireEvent.change(screen.getByPlaceholderText("Paste key"), { target: { value: "sk-x" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Maya's Tag: Maya's Tag didn't start");
    expect(screen.getByText(/Codex · API \(api.openai.com\) saved/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(setup).toHaveBeenCalledTimes(3));
    expect(setup.mock.calls[2][0][0]).toBe("t0klovr1-a0maya01");
  });

  it("opens an API to check it and switch its Tags back to the plan", async () => {
    history.replaceState(null, "", "?api=1");
    const api = demoBridge();
    history.replaceState(null, "", "/");
    const run = vi.spyOn(api, "tag");
    const setup = vi.spyOn(api, "setup");
    render(<AISettings api={api} tags={tags()} close={vi.fn()} apiConnections />);
    const row = await screen.findByRole("button", { name: "Codex · API (gateway.example.com)" });
    expect(row.textContent).not.toContain("Tag");
    fireEvent.click(row);
    expect(screen.getByText("Saved on this computer")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Check connection" }));
    expect(await screen.findByText(/checked on the first real request/)).toBeTruthy();
    expect(run).toHaveBeenCalledWith(["t0klovr1-a0maya01", "settings", "ai", "api", "check", "--backend", "codex", "--json"]);
    fireEvent.click(screen.getByRole("button", { name: "Switch back to my plan" }));
    await vi.waitFor(() => expect(setup).toHaveBeenCalledWith(
      ["t0klovr1-a0maya01", "settings", "ai", "api", "clear", "--backend", "codex", "--restart"], expect.any(Function), expect.any(Function)));
    expect(await screen.findByText(/Back on your Codex sign-in/, {}, { timeout: 4000 })).toBeTruthy();
    await vi.waitFor(() => expect(screen.queryByRole("button", { name: /Codex · API/ })).toBeNull(), { timeout: 4000 });
  });

  it("stays hidden when the installed Tag can't manage API connections", async () => {
    const api = demoBridge();
    render(<AISettings api={api} tags={tags()} close={vi.fn()} />);
    await screen.findAllByRole("button", { name: "Change account" });
    expect(screen.queryByRole("button", { name: /Add your own API/ })).toBeNull();
  });
});
