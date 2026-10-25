// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { deepLinkTarget, parseDeepLink } from "./deeplink";

describe("deepLinkTarget", () => {
  it("opens the linked Tag", () => {
    expect(deepLinkTarget("hover-tag://tag/t0klovr1-a0maya01")).toBe("t0klovr1-a0maya01");
    expect(deepLinkTarget("hover-tag://tag/default/")).toBe("default");
    expect(deepLinkTarget("hover-tag://tag/maya/thread/C0123ABCD/1761400000.123456")).toBe("maya");
  });
  it("ignores other links", () => {
    expect(deepLinkTarget("https://tag/default")).toBeNull();
    expect(deepLinkTarget("hover-tag://settings")).toBeNull();
    expect(deepLinkTarget("hover-tag://tag/")).toBeNull();
    expect(deepLinkTarget("hover-tag://tag/a%2F..%2Fb")).toBeNull();
    expect(deepLinkTarget("not a url")).toBeNull();
  });
});

describe("parseDeepLink", () => {
  it("addresses one Slack thread of a Tag", () => {
    expect(parseDeepLink("hover-tag://tag/maya/thread/C0123ABCD/1761400000.123456"))
      .toEqual({ tag: "maya", channel: "C0123ABCD", thread: "1761400000.123456" });
    expect(parseDeepLink("hover-tag://tag/maya/thread/D0123ABCD/1761400000.123456/"))
      .toEqual({ tag: "maya", channel: "D0123ABCD", thread: "1761400000.123456" });
    expect(parseDeepLink("hover-tag://tag/maya")).toEqual({ tag: "maya" });
  });
  it("rejects malformed thread links", () => {
    expect(parseDeepLink("hover-tag://tag/maya/thread/C0123ABCD")).toBeNull();
    expect(parseDeepLink("hover-tag://tag/maya/thread/c0123abcd/1761400000.123456")).toBeNull();
    expect(parseDeepLink("hover-tag://tag/maya/thread/C0123ABCD/not-a-ts")).toBeNull();
    expect(parseDeepLink("hover-tag://tag/maya/run/C0123ABCD/1761400000.123456")).toBeNull();
    expect(parseDeepLink("hover-tag://tag/maya/thread/C0123ABCD/1761400000.123456/extra")).toBeNull();
    expect(parseDeepLink("hover-tag://tag/maya/thread/%E0%A4%A/1761400000.123456")).toBeNull();
  });
});
