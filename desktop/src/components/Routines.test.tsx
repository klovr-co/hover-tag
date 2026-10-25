// Routines in Tag detail: the same commands as the CLI, with motion only for real changes.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { demoBridge } from "../lib/bridge";
import { parseList, type TagRow } from "../lib/protocol";
import { Routines } from "./Routines";

afterEach(cleanup);

async function open(index = 0) {
  const api = demoBridge();
  const rows: TagRow[] = parseList((await api.tag(["list", "--json"])).stdout);
  const row = rows[index];
  const calls: string[][] = [];
  const run = api.tag;
  api.tag = (args) => { calls.push(args); return run(args); };
  const say = vi.fn();
  render(<Routines api={api} row={row} say={say} />);
  return { api, row, calls, say };
}

describe("Routines", () => {
  it("lists routines without animating them in on open", async () => {
    const { row, calls } = await open();
    expect(await screen.findByText("Weekly PR digest")).toBeTruthy();
    expect(calls[0]).toEqual([row.id, "routine", "list", "--json"]);
    expect(document.querySelector(".rt-row.entering")).toBeNull();
    expect(document.querySelector(".rt-dot.ran")).toBeNull();
    expect(screen.getByText(/mon 09:00 · #launch · next/)).toBeTruthy();
  });

  it("says when the running Tag has stopped checking its routines", async () => {
    const api = demoBridge();
    const rows: TagRow[] = parseList((await api.tag(["list", "--json"])).stdout);
    const run = api.tag;
    api.tag = async (args) => {
      const result = await run(args);
      if (!args.includes("list") || !args.includes("routine")) return result;
      const list = JSON.parse(result.stdout);
      list.tag_running = true;
      list.scheduler = { ok: false, problem: "Routines aren't running: Can't read routines.json", last_check: null };
      return { ...result, stdout: JSON.stringify(list) };
    };
    render(<Routines api={api} row={rows[0]} say={vi.fn()} />);
    expect(await screen.findByText(/Routines aren't running: Can't read routines.json/)).toBeTruthy();
  });

  it("says when it can't reach Tag at all", async () => {
    const api = demoBridge();
    const rows: TagRow[] = parseList((await api.tag(["list", "--json"])).stdout);
    api.tag = () => Promise.reject(new Error("bridge gone"));
    render(<Routines api={api} row={rows[0]} say={vi.fn()} />);
    expect(await screen.findByText(/Couldn't reach Tag to read its routines/)).toBeTruthy();
  });

  it("pauses with the switch and says so", async () => {
    const { row, calls, say } = await open();
    fireEvent.click(await screen.findByRole("switch", { name: "Pause Weekly PR digest" }));
    await waitFor(() => expect(say).toHaveBeenCalledWith("Paused Weekly PR digest"));
    expect(calls).toContainEqual([row.id, "routine", "pause", expect.stringMatching(/^r-/), "--json"]);
    expect(await screen.findByText(/· paused$/)).toBeTruthy();
  });

  it("adds a routine that slides in, and approves it on save", async () => {
    const { row, calls, say } = await open(2);
    fireEvent.click(await screen.findByRole("button", { name: "Add a routine" }));
    fireEvent.click(screen.getByRole("button", { name: "Add and approve" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Give the routine a name.");
    expect(calls.some((args) => args.includes("add"))).toBe(false);
    fireEvent.change(screen.getByPlaceholderText("Weekly PR digest"), { target: { value: "On-call notes" } });
    fireEvent.click(screen.getByRole("button", { name: "every 2h" }));
    fireEvent.change(screen.getByLabelText("What to do each time"), { target: { value: "Summarise new incidents." } });
    fireEvent.click(screen.getByRole("button", { name: "Add and approve" }));
    await waitFor(() => expect(say).toHaveBeenCalledWith("Added On-call notes"));
    expect(calls).toContainEqual(expect.arrayContaining([row.id, "routine", "add", "--name", "On-call notes", "--schedule", "every 2h", "--channel", row.channels![0].id, "--via", "tag-app"]));
    expect(await screen.findByText("On-call notes")).toBeTruthy();
    expect(document.querySelector(".rt-row.entering")).toBeTruthy();
  });

  it("keeps the CLI's error beside the form", async () => {
    await open(2);
    fireEvent.click(await screen.findByRole("button", { name: "Add a routine" }));
    fireEvent.change(screen.getByPlaceholderText("Weekly PR digest"), { target: { value: "Too often" } });
    fireEvent.change(screen.getByLabelText("When"), { target: { value: "every 15m" } });
    fireEvent.change(screen.getByLabelText("What to do each time"), { target: { value: "p" } });
    fireEvent.click(screen.getByRole("button", { name: "Add and approve" }));
    expect((await screen.findByRole("alert")).textContent).toContain("at most once an hour");
    expect(screen.getByRole("button", { name: "Add and approve" })).toBeTruthy();
  });

  it("opens a routine's history and runs it now only while the Tag runs", async () => {
    const { row, calls, say } = await open();
    fireEvent.click(await screen.findByRole("button", { name: /Weekly PR digest/ }));
    expect(await screen.findByText(/Posted/)).toBeTruthy();
    const runNow = screen.getByRole("button", { name: "Run now" }) as HTMLButtonElement;
    expect(runNow.disabled).toBe(row.state !== "running");
    if (row.state === "running") {
      fireEvent.click(runNow);
      await waitFor(() => expect(say).toHaveBeenCalledWith("Running Weekly PR digest now"));
      expect(calls).toContainEqual([row.id, "routine", "run-now", expect.stringMatching(/^r-/), "--json"]);
    }
  });

  it("edits only what changed, re-approves it, and flashes the saved row once", async () => {
    const { row, calls, say } = await open();
    fireEvent.click(await screen.findByRole("button", { name: /Weekly PR digest/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("When"), { target: { value: "fri 17:30" } });
    fireEvent.click(screen.getByRole("button", { name: "Save and approve" }));
    await waitFor(() => expect(say).toHaveBeenCalledWith("Saved Weekly PR digest"));
    expect(calls).toContainEqual([row.id, "routine", "edit", expect.stringMatching(/^r-/), "--schedule", "fri 17:30", "--via", "tag-app", "--json"]);
    await waitFor(() => expect(document.querySelector(".rt-row.fresh")).toBeTruthy());
    await waitFor(() => expect(document.querySelector(".rt-row.fresh")).toBeNull(), { timeout: 2000 });
  });

  it("picks the timezone from a searchable list of real zones", async () => {
    const { row, calls, say } = await open();
    fireEvent.click(await screen.findByRole("button", { name: /Weekly PR digest/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const zone = screen.getByRole("combobox", { name: "Timezone" });
    expect((zone as HTMLInputElement).value).toBe("Europe/London");
    // A typo matches nothing and is never saved: leaving the field keeps the saved zone.
    fireEvent.focus(zone);
    fireEvent.change(zone, { target: { value: "Europe/Londn" } });
    expect(screen.getByText(/No timezone matches/)).toBeTruthy();
    fireEvent.blur(zone);
    expect((zone as HTMLInputElement).value).toBe("Europe/London");
    // Typing a city lists matching zones; picking one sets it.
    fireEvent.focus(zone);
    fireEvent.change(zone, { target: { value: "tokyo" } });
    fireEvent.click(screen.getByRole("option", { name: /Asia\/Tokyo|Asia Tokyo/ }));
    expect((zone as HTMLInputElement).value).toBe("Asia/Tokyo");
    fireEvent.focus(zone);
    fireEvent.change(zone, { target: { value: "new york" } });
    fireEvent.keyDown(zone, { key: "Enter" });
    expect((zone as HTMLInputElement).value).toBe("America/New_York");
    fireEvent.click(screen.getByRole("button", { name: "Save and approve" }));
    await waitFor(() => expect(say).toHaveBeenCalledWith("Saved Weekly PR digest"));
    expect(calls).toContainEqual([row.id, "routine", "edit", expect.stringMatching(/^r-/), "--timezone", "America/New_York", "--via", "tag-app", "--json"]);
  });

  it("opens what each run did: reply, files, steps, cost, Slack link, and a failure's log lines", async () => {
    const api = demoBridge();
    const rows: TagRow[] = parseList((await api.tag(["list", "--json"])).stdout);
    api.open = vi.fn();
    const showLogs = vi.fn();
    render(<Routines api={api} row={rows[0]} say={vi.fn()} showLogs={showLogs} />);
    fireEvent.click(await screen.findByRole("button", { name: /Weekly PR digest/ }));
    const history = await screen.findByRole("list", { name: "Weekly PR digest runs" });
    const runs = within(history).getAllByRole("button", { expanded: false });
    expect(runs.map((run) => run.textContent)).toEqual([expect.stringMatching(/Posted$/), expect.stringMatching(/Nothing new$/), expect.stringMatching(/Failed$/)]);
    fireEvent.click(runs[0]);
    expect(screen.getByText(/Four pull requests merged this week/)).toBeTruthy();
    expect(screen.getByText("pr-digest.md")).toBeTruthy();
    expect(screen.getByText(/Opus 5\.5 · 1m 37s · \$0\.18/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Open in Slack/ }));
    expect(api.open).toHaveBeenCalledWith(expect.stringMatching(/^slack:\/\/channel\?team=T0KLOVR1&id=C0LAUNCH1&message=\d+\.000100$/));
    fireEvent.click(screen.getByRole("button", { name: "Steps" }));
    expect(await screen.findByText("cat launch-plan.md")).toBeTruthy();
    fireEvent.click(within(history).getAllByRole("button", { expanded: false }).at(-1)!);
    expect(screen.getAllByText(/usage limit was reached\. Reference 7C2E91AB/).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Find 7C2E91AB in logs" }));
    expect(showLogs).toHaveBeenCalledWith("7C2E91AB");
  });

  it("shows all runs past the latest five, a page at a time", async () => {
    const api = demoBridge();
    const rows: TagRow[] = parseList((await api.tag(["list", "--json"])).stdout);
    const many = Array.from({ length: 27 }, (_, index) => ({ run_id: `r-${index}`, trigger: "schedule", scheduled_for: null,
      started: new Date(Date.now() - index * 3_600_000).toISOString(), ended: null, outcome: index % 2 ? "no_reply" : "posted" }));
    const run = api.tag;
    const calls: string[][] = [];
    api.tag = async (args) => {
      calls.push(args);
      const result = await run(args);
      if (args.includes("show")) return { ...result, stdout: JSON.stringify({ ...JSON.parse(result.stdout), history: many }) };
      if (args.includes("runs")) {
        const limit = Number(args[args.indexOf("--limit") + 1]), offset = Number(args[args.indexOf("--offset") + 1]);
        return { ...result, stdout: JSON.stringify({ ok: true, routine: {}, runs: many.slice(offset, offset + limit), total: many.length, has_more: offset + limit < many.length }) };
      }
      return result;
    };
    render(<Routines api={api} row={rows[0]} say={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /Weekly PR digest/ }));
    const history = await screen.findByRole("list", { name: "Weekly PR digest runs" });
    expect(within(history).getAllByRole("listitem")).toHaveLength(5);
    fireEvent.click(screen.getByRole("button", { name: "Show all 27 runs" }));
    await waitFor(() => expect(within(history).getAllByRole("listitem")).toHaveLength(20));
    // All runs scroll inside the routine; reaching the end loads older ones.
    expect(history.closest(".rt-runs")?.classList.contains("all")).toBe(true);
    expect(screen.getByText("20 of 27 runs")).toBeTruthy();
    fireEvent.scroll(history);
    await waitFor(() => expect(within(history).getAllByRole("listitem")).toHaveLength(27));
    expect(screen.queryByRole("button", { name: "Load older runs" })).toBeNull();
    expect(calls.filter((args) => args.includes("runs")).map((args) => args.slice(-3, -1))).toEqual([["--offset", "0"], ["--offset", "20"]]);
    fireEvent.click(screen.getByRole("button", { name: "Show fewer" }));
    expect(within(history).getAllByRole("listitem")).toHaveLength(5);
    expect(screen.getByRole("button", { name: "Show all 27 runs" })).toBeTruthy();
  });

  it("keeps the timezone list inside the window, opening upwards when there's no room below", async () => {
    const box = (top: number) => ({ top, bottom: top + 34, left: 0, right: 220, width: 220, height: 34, x: 0, y: top, toJSON: () => ({}) });
    const place = async (top: number, height: number) => {
      vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(box(top) as DOMRect);
      Object.defineProperty(window, "innerHeight", { configurable: true, value: height });
      const zone = screen.getByRole("combobox", { name: "Timezone" });
      fireEvent.focus(zone);
      const list = await screen.findByRole("listbox", { name: "Timezones" });
      const shown = { below: list.classList.contains("below"), max: list.style.maxHeight };
      fireEvent.blur(zone);
      vi.restoreAllMocks();
      return shown;
    };
    await open();
    fireEvent.click(await screen.findByRole("button", { name: /Weekly PR digest/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const tall = window.innerHeight;
    expect(await place(100, 900)).toEqual({ below: true, max: "340px" });
    expect(await place(700, 768)).toEqual({ below: false, max: "340px" });
    expect(await place(100, 300)).toEqual({ below: true, max: "154px" });
    Object.defineProperty(window, "innerHeight", { configurable: true, value: tall });
  });

  it("opens the routine it was asked to focus", async () => {
    const api = demoBridge();
    const rows: TagRow[] = parseList((await api.tag(["list", "--json"])).stdout);
    render(<Routines api={api} row={rows[0]} say={vi.fn()} focus="r-3f9a1c2e7b" />);
    expect(await screen.findByRole("list", { name: "Weekly PR digest runs" })).toBeTruthy();
  });

  it("asks before removing, and the row slides out", async () => {
    const { calls, say } = await open();
    fireEvent.click(await screen.findByRole("button", { name: /Weekly PR digest/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Remove" }));
    expect(screen.getByText("Remove Weekly PR digest and its history?")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "Remove" }).at(-1)!);
    await waitFor(() => expect(say).toHaveBeenCalledWith("Removed Weekly PR digest"));
    expect(calls.some((args) => args.includes("remove"))).toBe(true);
  });
});
