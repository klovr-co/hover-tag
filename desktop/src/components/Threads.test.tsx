// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Thread-first Tag detail: one list of every Tag's threads, the conversation in Markdown,
// replies that stay in the app unless posted to Slack, and questions answered from either side.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { type Bridge, demoBridge } from "../lib/bridge";
import { parseList, type TagRow } from "../lib/protocol";
import type { Tags } from "../lib/tags";
import { NOT_HERE, useThreads } from "../lib/threads";
import { TagDetail } from "./TagDetail";

afterEach(cleanup);

function Screen({ api, rows, initialThread, canAnswer = true }: { api: Bridge; rows: TagRow[]; initialThread?: { channel: string; thread: string }; canAnswer?: boolean }) {
  const threads = useThreads(api, rows, { enabled: true, seconds: 60 });
  const tags = { rows, busy: new Set<string>(), toggle: vi.fn(), workspace: vi.fn(), rename: vi.fn(async () => ""), describe: vi.fn(), refresh: vi.fn(), remove: vi.fn() } as unknown as Tags;
  return <TagDetail api={api} tags={tags} initial={rows[0].id} initialThread={initialThread} problems={{}} back={vi.fn()} add={vi.fn()} showSettings={vi.fn()}
    finishSetup={vi.fn()} openAI={vi.fn()} say={vi.fn()} threads={threads} canReply canAnswer={canAnswer} canRoutines />;
}

async function open(options: { initialThread?: { channel: string; thread: string }; canAnswer?: boolean; configure?: (rows: TagRow[]) => void } = {}) {
  const api = demoBridge();
  const rows: TagRow[] = parseList((await api.tag(["list", "--json"])).stdout);
  options.configure?.(rows);
  const calls: string[][] = [];
  const run = api.tag;
  api.tag = (args) => { calls.push(args); return run(args); };
  render(<Screen api={api} rows={rows} initialThread={options.initialThread} canAnswer={options.canAnswer} />);
  return { api, calls, rows };
}

const list = () => screen.getByRole("listbox", { name: "Threads" });

describe("Threads", { timeout: 20_000 }, () => {
  it("lists every Tag's threads with Needs you on top, and folds quiet finished ones into Settled", async () => {
    await open();
    await vi.waitFor(() => within(list()).getByText("Turn the launch notes into a checklist with owners"), { timeout: 5000 });
    const rows = within(list()).getAllByRole("option");
    expect(rows[0].textContent).toContain("Needs you");
    expect(rows[0].textContent).toContain("#launch");
    // Ops Tag's thread from another workspace is in the same list.
    expect(within(list()).getByText("Draft the on-call handover for launch week")).toBeTruthy();
    expect(within(list()).queryByText("Summarise this week's launch decisions")).toBeNull();
    fireEvent.click(within(list()).getByRole("button", { name: /Settled/ }));
    expect(within(list()).getByText("Summarise this week's launch decisions")).toBeTruthy();
    // Only "Needs you" is coloured.
    expect(document.querySelectorAll(".th-row .st.needs_you")).toHaveLength(1);
  }, 20_000);

  it("filters the list to one Tag and finds threads by channel", async () => {
    await open();
    await vi.waitFor(() => within(list()).getByText("Reconcile the October invoices"), { timeout: 5000 });
    fireEvent.click(screen.getByRole("button", { name: "All Tags" }));
    fireEvent.click(within(screen.getByRole("menu", { name: "Show threads of" })).getByRole("menuitemradio", { name: /Maya's Tag/ }));
    expect(within(list()).queryByText("Reconcile the October invoices")).toBeNull();
    fireEvent.change(screen.getByLabelText("Search threads"), { target: { value: "#launch" } });
    expect(within(list()).getAllByRole("option")).toHaveLength(1);
  }, 20_000);

  it("shows the conversation as Markdown, labels each person, folds the steps, and opens the side panel for files and memory", async () => {
    await open();
    await screen.findByRole("table", {}, { timeout: 5000 });
    expect(screen.getByText("Before launch").tagName).toBe("H4");
    expect(screen.getByText("Maya · in Slack", { exact: false })).toBeTruthy();
    expect(screen.getByText("Sam · in Slack", { exact: false })).toBeTruthy();
    const steps = screen.getByRole("button", { name: /worked for 25s/ });
    expect(screen.queryByText("Searching Slack…")).toBeNull();
    fireEvent.click(steps);
    expect(screen.getByText("Searching Slack…")).toBeTruthy();
    const panel = screen.getByRole("complementary", { name: "Files and memory" });
    expect(within(panel).getByText("launch-checklist.md")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Saved: Maya owns launch comms/ }));
    expect(within(panel).getByText("launch-comms")).toBeTruthy();
    fireEvent.click(within(panel).getByRole("button", { name: "Close panel" }));
    expect(screen.queryByRole("complementary")).toBeNull();
  });

  it("opens the side panel only when a thread has something to show", async () => {
    await open({ initialThread: { channel: "D0MAYA001", thread: "1792401200.000100" } });
    await screen.findByText("first 200 people", { exact: false }, { timeout: 5000 });
    expect(screen.queryByRole("complementary")).toBeNull();
  });

  it("answers a waiting question from the app, and it stops waiting", async () => {
    const { calls, rows } = await open();
    fireEvent.click(await screen.findByRole("button", { name: "Run this once" }, { timeout: 5000 }));
    await vi.waitFor(() => expect(calls).toContainEqual([rows[0].id, "thread", "C0LAUNCH1", "1792487642.000100", "answer", "p0runreq01", "run_once", "--json"]));
    expect(await screen.findByText("Maya: Run this once")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Run this once" })).toBeNull();
  });

  it("says to answer in Slack when this Tag can't take answers from the app", async () => {
    await open({ canAnswer: false });
    expect(await screen.findByText(/Answer it in Slack/, {}, { timeout: 5000 })).toBeTruthy();
    expect((screen.getByRole("button", { name: "Run this once" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("keeps a reply in the app unless this one message is also posted to Slack", async () => {
    const { calls, rows } = await open({ initialThread: { channel: "D0MAYA001", thread: "1792401200.000100" } });
    const box = await screen.findByLabelText("Reply", {}, { timeout: 5000 });
    fireEvent.change(box, { target: { value: "Keep 20 places for partners." } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await vi.waitFor(() => expect(calls).toContainEqual([rows[0].id, "thread", "D0MAYA001", "1792401200.000100", "reply", "--text=Keep 20 places for partners.", "--json"]));
    const sent = (await screen.findByText(/You · in Tag.app ·/)).closest(".th-msg")!;
    expect(sent.textContent).toContain("Keep 20 places for partners.");
    fireEvent.change(screen.getByLabelText("Reply"), { target: { value: "Post this one" } });
    fireEvent.click(screen.getByRole("switch", { name: "Also post in Slack" }));
    expect(screen.getByText("Also posted in the Slack thread")).toBeTruthy();
    fireEvent.keyDown(screen.getByLabelText("Reply"), { key: "Enter" });
    await vi.waitFor(() => expect(calls).toContainEqual([rows[0].id, "thread", "D0MAYA001", "1792401200.000100", "reply", "--text=Post this one", "--post-to-slack", "--json"]));
    // The switch is per message.
    await vi.waitFor(() => expect(screen.getByRole("switch", { name: "Also post in Slack" }).getAttribute("aria-checked")).toBe("false"));
  });

  it("asks to start a stopped Tag before replying", async () => {
    await open({ configure: (rows) => { rows[0].state = "stopped"; } });
    expect(await screen.findByText("Maya's Tag replies only while it runs.", {}, { timeout: 5000 })).toBeTruthy();
    expect(screen.queryByLabelText("Reply")).toBeNull();
  });

  it("says when a linked thread isn't on this computer", async () => {
    await open({ initialThread: { channel: "C0LAUNCH1", thread: "1.000001" } });
    expect(await screen.findByText(NOT_HERE, {}, { timeout: 5000 })).toBeTruthy();
  });

  it("shows threads from before transcripts as a preview, with no reply box", async () => {
    await open({ initialThread: { channel: "C0LAUNCH1", thread: "1791800000.000100" } });
    expect(await screen.findByText(/only a short preview/, {}, { timeout: 5000 })).toBeTruthy();
    expect(screen.queryByLabelText("Reply")).toBeNull();
  });

  it("explains that new threads start in Slack for now", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "New thread" }));
    expect(screen.getByRole("dialog", { name: "New thread" }).textContent).toContain("New threads start in Slack for now");
  });

  it("keeps Knowledge as its own tab, and Channels, Logs and Details under the gear", async () => {
    await open();
    const views = screen.getByRole("tablist", { name: "View" });
    expect(within(views).getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Threads", "Knowledge"]);
    fireEvent.click(within(views).getByRole("tab", { name: "Knowledge" }));
    expect(screen.getByText("Knowledge is on its way")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Maya's Tag settings" }));
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(expect.arrayContaining(["Channels 2", "Routines", "Logs", "Details"]));
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Maya's Tag settings" })); });
    expect(within(views).getByRole("tab", { name: "Threads" }).getAttribute("aria-selected")).toBe("true");
  });
});
