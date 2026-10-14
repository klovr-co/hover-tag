// Routines in Tag detail: the same commands as the CLI, with motion only for real changes.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
