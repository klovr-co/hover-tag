// Works with: the owner ticks the Tags and apps that can give a Tag tasks; nothing is ticked for them.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { demoBridge } from "../lib/bridge";
import { parseList, type TagRow } from "../lib/protocol";
import type { Tags } from "../lib/tags";
import { TagDetail } from "./TagDetail";

afterEach(cleanup);

async function open(canWorksWith = true) {
  const api = demoBridge();
  const rows: TagRow[] = parseList((await api.tag(["list", "--json"])).stdout);
  const calls: string[][] = [];
  const run = api.tag;
  api.tag = (args) => { calls.push(args); return run(args); };
  const tags = { rows, busy: new Set<string>(), toggle: vi.fn(), workspace: vi.fn(), rename: vi.fn(async () => ""),
    describe: vi.fn(async () => ""), refresh: vi.fn(), remove: vi.fn(async () => true) } as unknown as Tags;
  const say = vi.fn();
  render(<TagDetail api={api} tags={tags} initial={rows[0].id} problems={{}} back={vi.fn()} add={vi.fn()} showSettings={vi.fn()}
    finishSetup={vi.fn()} openAI={vi.fn()} say={say} canWorksWith={canWorksWith} />);
  fireEvent.click(screen.getByRole("tab", { name: "Details" }));
  return { calls, say, rows };
}

const worksWithCalls = (calls: string[][]) => calls.filter((args) => args[2] === "works-with");

describe("Works with", () => {
  it("starts empty and lists the owner's Tags and the workspace's apps, all unticked", async () => {
    const { calls, rows } = await open();
    expect(await screen.findByText("Nobody yet. Empty until you add someone.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    const picker = screen.getByRole("group", { name: "Choose who this Tag works with" });
    const research = await within(picker).findByRole("checkbox", { name: /Research Tag/ });
    expect(within(picker).getByRole("checkbox", { name: /Hover/ })).toBeTruthy();
    expect(within(picker).getAllByRole("checkbox").every((box) => !(box as HTMLInputElement).checked)).toBe(true);
    // Only Tags in the same Slack workspace are offered.
    expect(within(picker).queryByRole("checkbox", { name: /Ops Tag/ })).toBeNull();
    expect(research.closest("label")?.textContent).toContain("your Tag");
    expect(worksWithCalls(calls).map((args) => args.slice(3))).toEqual([["--json"], ["candidates", "--json"]]);
    expect(worksWithCalls(calls)[0][0]).toBe(rows[0].id);
  });

  it("saves the ticked Tags and apps and restarts a running Tag", async () => {
    const { calls, say } = await open();
    fireEvent.click(await screen.findByRole("button", { name: "Add" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /Research Tag/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Hover/ }));
    expect(screen.getByText("Saving restarts Maya's Tag.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Save and restart" }));
    const list = await screen.findByRole("list", { name: "Works with" });
    expect(within(list).getAllByRole("listitem").map((item) =>
      `${item.querySelector(".ww-name")?.textContent} · ${item.querySelector(".meta")?.textContent}`)).toEqual(["Research Tag · your Tag", "Hover · app"]);
    const saved = worksWithCalls(calls).find((args) => args[3] === "set")!;
    expect(saved.slice(3)).toEqual(["set", "U00RESE02", "U0HOVER01", "--restart", "--json"]);
    await waitFor(() => expect(say).toHaveBeenCalledWith("Maya's Tag restarted. The change applies now."));
  });

  it("adds a pasted ID as an app and unticks to remove", async () => {
    const { calls } = await open();
    fireEvent.click(await screen.findByRole("button", { name: "Add" }));
    const id = screen.getByRole("textbox", { name: "Member or bot ID" });
    fireEvent.change(id, { target: { value: "not an id" } });
    expect((screen.getByRole("button", { name: "Add" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(id, { target: { value: "b0legacy1" } });
    fireEvent.keyDown(id, { key: "Enter" });
    expect((screen.getByRole("checkbox", { name: /B0LEGACY1/ }) as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Save and restart" }));
    await screen.findByRole("list", { name: "Works with" });
    fireEvent.click(screen.getByRole("button", { name: "Change" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /B0LEGACY1/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save and restart" }));
    expect(await screen.findByText("Nobody yet. Empty until you add someone.")).toBeTruthy();
    expect(worksWithCalls(calls).filter((args) => args[3] === "set").map((args) => args.slice(4, -2)))
      .toEqual([["B0LEGACY1"], []]);
  });

  it("is hidden when the installed Tag can't change it", async () => {
    const { calls } = await open(false);
    await screen.findByText("Working folder");
    expect(screen.queryByText("Works with")).toBeNull();
    expect(worksWithCalls(calls)).toEqual([]);
  });
});
