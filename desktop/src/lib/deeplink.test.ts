// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import threads from "../../../protocol/examples/threads.json";
import { deepLinkTarget, deepLinkThread, threadLink } from "./deeplink";

describe("deepLinkTarget", () => {
  it("opens the linked Tag", () => {
    expect(deepLinkTarget("hover-tag://tag/t0klovr1-a0maya01")).toEqual({ tag: "t0klovr1-a0maya01", channel: null, thread: null });
    expect(deepLinkTarget("hover-tag://tag/default/")).toEqual({ tag: "default", channel: null, thread: null });
  });
  it("opens one thread of a Tag", () => {
    expect(deepLinkTarget("hover-tag://tag/t0klovr1-a0maya01/thread/C0LAUNCH1/1792487642.000100"))
      .toEqual({ tag: "t0klovr1-a0maya01", channel: "C0LAUNCH1", thread: "1792487642.000100" });
    expect(deepLinkThread("hover-tag://tag/default")).toBeNull();
    expect(deepLinkThread(threadLink("t1-a1", "D0MAYA001", "1.000001"))).toEqual({ tag: "t1-a1", channel: "D0MAYA001", thread: "1.000001" });
  });
  it("makes the same link the CLI reports", () => {
    for (const row of threads.threads) expect(threadLink(threads.tag, row.channel, row.thread_ts)).toBe(row.link);
  });
  it("ignores other links", () => {
    expect(deepLinkTarget("https://tag/default")).toBeNull();
    expect(deepLinkTarget("hover-tag://settings")).toBeNull();
    expect(deepLinkTarget("hover-tag://tag/")).toBeNull();
    expect(deepLinkTarget("hover-tag://tag/a%2F..%2Fb")).toBeNull();
    expect(deepLinkTarget("hover-tag://tag/a/thread/C1/not-a-ts")).toBeNull();
    expect(deepLinkTarget("hover-tag://tag/a/thread/x1/1.000001")).toBeNull();
    expect(deepLinkTarget("hover-tag://tag/a/other/C1/1.000001")).toBeNull();
    expect(deepLinkTarget("hover-tag://tag/a/thread/C1/1.000001/more")).toBeNull();
    expect(deepLinkTarget("hover-tag://tag/%E0%A4%A")).toBeNull();
    expect(deepLinkTarget("not a url")).toBeNull();
  });
});
