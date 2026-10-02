// When Tag can't be run at all, the window must say so and never stay busy.
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { demoBridge } from "./bridge";
import { useTags } from "./tags";

describe("useTags", () => {
  it("reports a failed tag call and clears the busy state", async () => {
    const api = demoBridge();
    api.tag = async () => { throw new Error("Tag isn't installed yet."); };
    const { result } = renderHook(() => useTags(api, false));
    let ok = true;
    await act(async () => { ok = await result.current.setAutostart(true); });
    expect(ok).toBe(false);
    expect(result.current.busy.size).toBe(0);
    expect(result.current.error).toMatch(/Tag isn't installed yet/);
  });

  it("shows an unreadable list as an error, not a crash", async () => {
    const api = demoBridge();
    api.tag = async () => ({ code: 0, stdout: "not json", stderr: "" });
    const { result } = renderHook(() => useTags(api, true));
    await waitFor(() => expect(result.current.error).toBe("Couldn't read your Tags."));
  });
});
