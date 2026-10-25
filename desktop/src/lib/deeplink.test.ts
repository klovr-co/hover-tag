// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { deepLinkTarget } from "./deeplink";

describe("deepLinkTarget", () => {
  it("opens the linked Tag", () => {
    expect(deepLinkTarget("hover-tag://tag/t0klovr1-a0maya01")).toBe("t0klovr1-a0maya01");
    expect(deepLinkTarget("hover-tag://tag/default/")).toBe("default");
  });
  it("ignores other links", () => {
    expect(deepLinkTarget("https://tag/default")).toBeNull();
    expect(deepLinkTarget("hover-tag://settings")).toBeNull();
    expect(deepLinkTarget("hover-tag://tag/")).toBeNull();
    expect(deepLinkTarget("hover-tag://tag/a%2F..%2Fb")).toBeNull();
    expect(deepLinkTarget("not a url")).toBeNull();
  });
});
