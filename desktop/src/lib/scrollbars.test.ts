// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
import { afterEach, describe, expect, it } from "vitest";
import { installOverlayScrollbars, thumbFor } from "./scrollbars";

let remove = () => {};
afterEach(() => { remove(); document.body.innerHTML = ""; });

const scroller = (top: number) => {
  const el = document.createElement("div");
  Object.defineProperties(el, { scrollHeight: { value: 1000 }, clientHeight: { value: 250 }, scrollTop: { value: top, writable: true } });
  el.getBoundingClientRect = () => ({ top: 100, bottom: 350, left: 0, right: 400, width: 400, height: 250, x: 0, y: 100, toJSON: () => ({}) });
  document.body.appendChild(el);
  return el;
};

describe("overlay scrollbars", () => {
  it("sizes the thumb to the visible share and moves it with the scroll", () => {
    expect(thumbFor(0, 1000, 250, 200)).toEqual({ size: 50, offset: 0 });
    expect(thumbFor(750, 1000, 250, 200)).toEqual({ size: 50, offset: 150 });
    expect(thumbFor(375, 1000, 250, 200)?.offset).toBe(75);
  });
  it("keeps a long page's thumb big enough to grab", () => {
    expect(thumbFor(0, 100000, 250, 200)?.size).toBe(28);
  });
  it("shows nothing when the content fits", () => {
    expect(thumbFor(0, 250, 250, 200)).toBeNull();
  });
  it("floats a thumb over whatever scrolls, inside its right edge", () => {
    remove = installOverlayScrollbars();
    const el = scroller(375);
    el.dispatchEvent(new Event("scroll"));
    const thumb = document.querySelector<HTMLElement>(".oscroll")!;
    expect(thumb.classList.contains("shown")).toBe(true);
    expect(thumb.style.left).toBe("397px");
    expect(thumb.style.height).toBe("61px");
    expect(parseFloat(thumb.style.top)).toBeCloseTo(103 + (244 - 61) / 2);
  });
  it("cleans up after itself", () => {
    installOverlayScrollbars()();
    expect(document.querySelector(".oscroll")).toBeNull();
  });
});
