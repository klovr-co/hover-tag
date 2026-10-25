// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// The app reads the same example payloads the CLI's tests check, so the two can't drift apart.
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import notFound from "../../../protocol/examples/thread-not-found.json";
import threadExample from "../../../protocol/examples/thread.json";
import threadsExample from "../../../protocol/examples/threads.json";
import type { Bridge } from "./bridge";
import type { TagRow } from "./protocol";
import { ago, arrange, needsYouNotice, NOT_HERE, parseThread, parseThreads, placeOf, threadTitle, useThreads, type ListedThread } from "./threads";

const now = new Date("2026-10-26T12:00:00Z");
const row = (over: Partial<ListedThread>): ListedThread => ({
  ...(threadsExample.threads[0] as ListedThread), tag: "t1", ...over,
});
const hoursAgo = (hours: number) => new Date(now.getTime() - hours * 3600_000).toISOString();

describe("threads", () => {
  it("reads the example payloads", () => {
    const listed = parseThreads(JSON.stringify(threadsExample));
    expect(listed.retention_days).toBe(90);
    expect(listed.threads.map(placeOf)).toEqual(["DM", "#launch"]);
    const read = parseThread(0, JSON.stringify(threadExample));
    if (!("thread" in read)) throw new Error("expected a thread");
    expect(read.thread.items.map((item) => item.type)).toEqual(["message", "work", "answer", "message", "prompt"]);
    expect(read.thread.prompts[0].choices.map((c) => c.id)).toEqual(["run_once", "allow_thread", "ignore"]);
    expect(parseThread(1, JSON.stringify(notFound))).toEqual({ missing: NOT_HERE });
    expect(notFound.error).toBe(NOT_HERE);
    expect(() => parseThread(1, JSON.stringify({ ok: false, error: "Boom" }))).toThrow("Boom");
  });

  it("puts Needs you on top and folds quiet finished threads into Settled", () => {
    const { active, settled } = arrange([
      row({ thread_ts: "1.1", status: "done", updated_at: hoursAgo(2) }),
      row({ thread_ts: "1.2", status: "needs_you", updated_at: hoursAgo(30) }),
      row({ thread_ts: "1.3", status: "done", updated_at: hoursAgo(48) }),
      row({ thread_ts: "1.4", status: "failed", updated_at: hoursAgo(72) }),
      row({ thread_ts: "1.5", status: "working", updated_at: hoursAgo(1) }),
    ], now);
    expect(active.map((t) => t.thread_ts)).toEqual(["1.2", "1.5", "1.1", "1.4"]);
    expect(settled.map((t) => t.thread_ts)).toEqual(["1.3"]);
  });

  it("names threads and times for the list", () => {
    expect(threadTitle(row({ title: null, last_answer_preview: "Booked Tue 11:00" }))).toBe("Booked Tue 11:00");
    expect(threadTitle(row({ title: null, last_answer_preview: null }))).toBe("Thread");
    expect(placeOf(row({ dm: false, channel: "C1", channel_name: null }))).toBe("#C1");
    expect([ago(hoursAgo(0), now), ago(hoursAgo(0.5), now), ago(hoursAgo(5), now), ago(hoursAgo(50), now)]).toEqual(["now", "30m", "5h", "2d"]);
    const tag = { id: "t1", valid: true, slack_name: "Maya's Tag" } as TagRow;
    expect(needsYouNotice(row({ title: "Add CTO", channel_name: "ops", dm: false }), tag)).toEqual({ title: "Maya's Tag needs you", body: "Add CTO · #ops" });
  });

  it("notifies once when a thread starts waiting, never for those already waiting, and reads stopped Tags once", async () => {
    vi.useFakeTimers();
    try {
      let status = "done";
      const calls: string[] = [];
      const api = { tag: vi.fn(async (args: string[]) => {
        calls.push(args[0]);
        const threads = args[0] === "t1"
          ? [row({ thread_ts: "1.1", status: "needs_you", updated_at: "a" }), row({ thread_ts: "1.2", status, updated_at: status })]
          : [row({ thread_ts: "2.1" })];
        return { code: 0, stderr: "", stdout: JSON.stringify({ schema_version: 1, ok: true, threads }) };
      }) } as unknown as Bridge;
      const tags = [{ id: "t1", valid: true, state: "running" }, { id: "t2", valid: true, state: "stopped" }] as TagRow[];
      const notify = vi.fn();
      const { result } = renderHook(() => useThreads(api, tags, { enabled: true, seconds: 10, notify }));
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(result.current.loaded).toBe(true);
      expect(result.current.rows.map((t) => `${t.tag}/${t.thread_ts}`)).toEqual(["t1/1.1", "t1/1.2", "t2/2.1"]);
      expect(notify).not.toHaveBeenCalled();
      status = "needs_you";
      await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
      expect(notify).toHaveBeenCalledTimes(1);
      expect(notify.mock.calls[0][0].thread_ts).toBe("1.2");
      await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
      expect(notify).toHaveBeenCalledTimes(1);
      expect(calls.filter((id) => id === "t2")).toHaveLength(1);
    } finally { vi.useRealTimers(); }
  });
});
