// When Tag can't be run at all, the window must say so and never stay busy.
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { demoBridge, type Bridge } from "./bridge";
import { startTag, useTags } from "./tags";

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
    await waitFor(() => expect(result.current.error).toBe("Couldn't read your Tags. Tag printed no JSON"));
    expect(result.current.loaded).toBe(false);
  });

  it.each([
    { code: 126, stdout: "", stderr: "tag: missing-python: No such file or directory\n" },
    { code: 1, stdout: '{"ok":false,"error":"The installation is incomplete."}', stderr: "" },
  ])("shows the CLI's failure and clears it after retry ($code)", async (failure) => {
    const api = demoBridge();
    const working = api.tag;
    api.tag = async () => failure;
    const { result } = renderHook(() => useTags(api, false));
    await act(async () => { await result.current.refresh(); });
    expect(result.current.loaded).toBe(false);
    expect(result.current.error).toContain(failure.stderr.trim() || "The installation is incomplete.");
    api.tag = working;
    await act(async () => { await result.current.refresh(); });
    expect(result.current.loaded).toBe(true);
    expect(result.current.error).toBe("");
  });

  it("keeps action failures when the list recovers, then clears the stale list failure", async () => {
    const api = demoBridge();
    const working = api.tag;
    api.tag = async () => { throw "Couldn't run Tag: permission denied"; };
    const { result } = renderHook(() => useTags(api, false));
    await act(async () => { await result.current.refresh(); });
    expect(result.current.error).toContain("Couldn't run Tag: permission denied");
    act(() => result.current.setError("Couldn't stop this Tag."));
    await act(async () => { await result.current.refresh(); });
    expect(result.current.error).toBe("Couldn't stop this Tag.");
    api.tag = working;
    await act(async () => { await result.current.refresh(); });
    expect(result.current.error).toBe("Couldn't stop this Tag.");
    act(() => result.current.setError(""));
    expect(result.current.error).toBe("");
  });
});

describe("startTag", () => {
  it("follows start step by step and takes the outcome from its result line", async () => {
    const follow = vi.fn(async (_args: string[], onLine: (line: string) => void, onExit: (code: number, stderr: string) => void) => {
      onLine('{"type": "progress", "step": "memory", "label": "Memory", "state": "running", "text": "…"}');
      onLine('{"type": "result", "status": "failed", "error": "Slack bridge did not become ready"}');
      onExit(1, "");
      return { send: () => {}, stop: () => {} };
    });
    const lines: string[] = [];
    expect(await startTag({ follow } as unknown as Bridge, "t1", (line) => lines.push(line))).toBe("Slack bridge did not become ready");
    expect(follow.mock.calls[0][0]).toEqual(["t1", "start"]);
    expect(lines).toHaveLength(1);
  });

  it("starts once and says what went wrong", async () => {
    const tag = vi.fn().mockResolvedValue({ code: 1, stdout: "", stderr: "Error: Slack token revoked" });
    expect(await startTag({ tag } as unknown as Bridge, "t1")).toBe("Slack token revoked");
    expect(tag).toHaveBeenCalledTimes(1);
    expect(tag).toHaveBeenCalledWith(["t1", "start"]);
    tag.mockResolvedValue({ code: 0, stdout: "", stderr: "" });
    expect(await startTag({ tag } as unknown as Bridge, "t1")).toBe("");
  });
});
