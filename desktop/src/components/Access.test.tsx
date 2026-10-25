// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Who can work with a Tag: two separate permissions, each with per-channel overrides.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { demoBridge } from "../lib/bridge";
import { AccessRows } from "./Access";

afterEach(cleanup);

async function open() {
  const api = demoBridge();
  const calls: string[][] = [];
  const run = api.tag;
  api.tag = (args) => { calls.push(args); return run(args); };
  const say = vi.fn();
  render(<dl><AccessRows api={api} tag="maya" channels={[{ id: "C0123ABCD", name: "launch" }]} say={say} /></dl>);
  await screen.findByText("Who can give it information");
  return { calls, say };
}

describe("AccessRows", () => {
  it("names both permissions in full, with their defaults", async () => {
    const { calls } = await open();
    expect(calls[0]).toEqual(["maya", "settings", "access", "--json"]);
    expect(screen.getByText("Who can make it run work")).toBeTruthy();
    expect(screen.getByRole("radiogroup", { name: "Who can give it information" }).querySelector("[aria-checked=true]")?.textContent)
      .toBe("Anyone in the thread");
    expect(screen.getByRole("radiogroup", { name: "Who can make it run work" }).querySelector("[aria-checked=true]")?.textContent)
      .toBe("Only me");
    expect(screen.queryByText(/^Allow/)).toBeNull();
  });

  it("saves one permission at a time, for the Tag or one channel", async () => {
    const { calls, say } = await open();
    fireEvent.click(screen.getAllByRole("radio", { name: "Only me" })[0]);
    await waitFor(() => expect(say).toHaveBeenCalledWith("Who can give it information: Only me"));
    expect(calls.at(-1)).toEqual(["maya", "settings", "access", "inform", "owner", "--json"]);
    fireEvent.change(screen.getByLabelText("Who can make it run work in launch"), { target: { value: "authorized" } });
    await waitFor(() => expect(say).toHaveBeenCalledWith("Who can make it run work in launch: People I've authorised"));
    expect(calls.at(-1)).toEqual(["maya", "settings", "access", "run", "authorized", "C0123ABCD", "--json"]);
  });
});
