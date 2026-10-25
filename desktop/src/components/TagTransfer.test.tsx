// Export Tag and Import run the CLI with the file the person chose.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { demoBridge } from "../lib/bridge";
import { parseList, type TagRow } from "../lib/protocol";
import { exportArgs, exportFileName, importArgs, transferError } from "../lib/transfer";
import { ExportTag, ImportTag } from "./TagTransfer";

afterEach(cleanup);

async function setup() {
  const api = demoBridge();
  const rows: TagRow[] = parseList((await api.tag(["list", "--json"])).stdout);
  const calls: string[][] = [];
  const run = api.tag;
  api.tag = (args) => { calls.push(args); return run(args); };
  return { api, row: rows[0], calls, say: vi.fn() };
}

describe("transfer helpers", () => {
  it("build the commands and a file name", () => {
    expect(exportArgs("t1", "/tmp/a b.hovertag")).toEqual(["t1", "export", "--output=/tmp/a b.hovertag", "--json"]);
    expect(exportArgs("t1", "/x", true)).toContain("--without-history");
    expect(importArgs("/x.hovertag")).toEqual(["import", "/x.hovertag", "--json"]);
    expect(exportFileName({ id: "t1", slack_name: "Maya's Tag" } as TagRow, new Date("2026-10-26T09:00:00Z"))).toBe("mayas-tag-2026-10-26.hovertag");
    expect(transferError('{"ok": false, "error": "No", "code": "history_has_token"}', "")).toEqual({ text: "No", code: "history_has_token" });
  });
});

describe("ExportTag", () => {
  it("saves to the chosen file and says what it holds", async () => {
    const { api, row, calls, say } = await setup();
    render(<ExportTag api={api} row={row} say={say} />);
    fireEvent.click(screen.getByText("Export Tag…"));
    expect(await screen.findByText(/Saved 2 memory files, 1 skills and 0 threads with history/)).toBeTruthy();
    expect(calls.at(-1)![1]).toBe("export");
    expect(calls.at(-1)![2]).toMatch(/^--output=\/Users\/maya\/Downloads\/.*\.hovertag$/);
    expect(say).toHaveBeenCalledWith(expect.stringMatching(/no passwords or tokens/));
  });

  it("offers an export without history when history holds a token", async () => {
    const { api, row, say } = await setup();
    const run = api.tag;
    api.tag = async (args) => args.includes("--without-history") ? run(args)
      : { code: 1, stdout: JSON.stringify({ ok: false, error: "History holds a token.", code: "history_has_token" }), stderr: "" };
    render(<ExportTag api={api} row={row} say={say} />);
    fireEvent.click(screen.getByText("Export Tag…"));
    fireEvent.click(await screen.findByText("Export without history"));
    await waitFor(() => expect(say).toHaveBeenCalled());
  });

  it("does nothing when the person cancels", async () => {
    const { api, row, calls, say } = await setup();
    api.saveFile = async () => null;
    render(<ExportTag api={api} row={row} say={say} />);
    fireEvent.click(screen.getByText("Export Tag…"));
    await new Promise((r) => setTimeout(r, 20));
    expect(calls).toEqual([]);
  });
});

describe("ImportTag", () => {
  it("imports the chosen file and hands the new Tag on to setup", async () => {
    const { api, calls, say } = await setup();
    const onImported = vi.fn();
    render(<ImportTag api={api} say={say} onImported={onImported} />);
    fireEvent.click(screen.getByText("Import"));
    await waitFor(() => expect(onImported).toHaveBeenCalledWith(expect.objectContaining({ tag: "mayas-tag-2", next: "tag mayas-tag setup" })));
    expect(calls.at(-1)).toEqual(["import", "/Users/maya/Downloads/mayas-tag-2026-10-26.hovertag", "--json"]);
    expect(say).toHaveBeenCalledWith(expect.stringMatching(/Stop it on the old computer/));
  });
});
