// Tag detail: the feed shows only Tag's own records, and model changes wait for a click.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { demoBridge } from "../lib/bridge";
import { parseList, type TagRow } from "../lib/protocol";
import type { Tags } from "../lib/tags";
import { activityText, TagDetail } from "./TagDetail";

afterEach(cleanup);

async function open(tab?: string) {
  const api = demoBridge();
  const rows: TagRow[] = parseList((await api.tag(["list", "--json"])).stdout);
  const calls: string[][] = [];
  const run = api.tag;
  api.tag = (args) => { calls.push(args); return run(args); };
  const tags = { rows, busy: new Set<string>(), toggle: vi.fn(), workspace: vi.fn(), rename: vi.fn(async () => true), refresh: vi.fn() } as unknown as Tags;
  const say = vi.fn();
  render(<TagDetail api={api} tags={tags} initial={rows[0].id} problems={{}} back={vi.fn()} add={vi.fn()} showSettings={vi.fn()}
    finishSetup={vi.fn()} openAI={vi.fn()} say={say} />);
  if (tab) fireEvent.click(screen.getByRole("tab", { name: tab }));
  return { calls, say, rows };
}

describe("Tag detail", () => {
  it("shows the Tag's recorded replies, and nothing it didn't do", async () => {
    await open();
    expect(await screen.findByText("#launch")).toBeTruthy();
    expect(screen.getByText("Today")).toBeTruthy();
    expect(screen.queryByText(/Connected to Slack|Memory ready/)).toBeNull();
    expect(activityText({ at: "", kind: "failed", channel: "D1", channel_name: null, dm: true })).toBe("Couldn't finish a request in a direct message");
  });

  it("lists the workspace's Tags and channels, and a channel shows its Tags", async () => {
    await open();
    expect(screen.getByRole("button", { name: /Research Tag/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^#\s*general$/ }));
    expect(screen.getByText("1 Tag answers here")).toBeTruthy();
  });

  it("asks before restarting a running Tag to use a new thinking level", async () => {
    const { calls } = await open("Details");
    fireEvent.click(await screen.findByRole("radio", { name: "High" }, { timeout: 5000 }));
    expect(screen.getByText("Restart Maya's Tag to use GPT-5.5 · High thinking.")).toBeTruthy();
    expect(calls.some((args) => args.includes("model"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Save and restart" }));
    await vi.waitFor(() => expect(calls).toContainEqual([expect.any(String), "settings", "ai", "model", "codex:gpt-5.5", "--effort", "high", "--restart", "--json"]));
  });

  it("Cancel puts the saved level back", async () => {
    await open("Details");
    await screen.findByRole("radio", { name: "Low" }, { timeout: 5000 });
    const radios = screen.getAllByRole("radio");
    const saved = radios.find((radio) => radio.getAttribute("aria-checked") === "true")!;
    fireEvent.click(radios.find((radio) => radio !== saved)!);
    expect(saved.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(saved.getAttribute("aria-checked")).toBe("true");
    expect(screen.queryByText(/Restart Maya's Tag/)).toBeNull();
  });
});
