// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { windowFitter, type View } from "./fit";

/** A window whose page gets `size - titleBar`, resized after a moment like a real one. */
function fakeWindow(titleBar: number) {
  const listeners = new Set<() => void>();
  const view = {
    innerWidth: 520, innerHeight: 600,
    addEventListener: (_: "resize", fn: () => void) => listeners.add(fn),
    removeEventListener: (_: "resize", fn: () => void) => listeners.delete(fn),
  };
  const sizes: number[] = [];
  const setSize = async (width: number, height: number) => {
    sizes.push(height);
    await new Promise((resolve) => setTimeout(resolve, 5));
    const next = { innerWidth: width, innerHeight: height - titleBar };
    if (next.innerWidth === view.innerWidth && next.innerHeight === view.innerHeight) return;
    Object.assign(view, next);
    for (const fn of [...listeners]) fn();
  };
  return { view: view as View, sizes, setSize };
}

describe("window fitting", () => {
  it("lands on the latest height when requests overlap", async () => {
    const win = fakeWindow(0);
    const fit = windowFitter(win.setSize, win.view, 20);
    void fit(520, 400);
    void fit(520, 520);
    await fit(520, 583);
    expect(win.view.innerHeight).toBe(583);
  });

  it("learns a title bar once and keeps the page at the height it asked for", async () => {
    const win = fakeWindow(28);
    const fit = windowFitter(win.setSize, win.view, 20);
    await fit(520, 583);
    expect(win.view.innerHeight).toBe(583);
    await fit(520, 640);
    expect(win.view.innerHeight).toBe(640);
    expect(win.sizes).toEqual([583, 611, 668]);
  });

  it("stays between the smallest and tallest window", async () => {
    const win = fakeWindow(0);
    const fit = windowFitter(win.setSize, win.view, 20);
    await fit(520, 5000);
    expect(win.view.innerHeight).toBe(860);
    await fit(520, 10);
    expect(win.view.innerHeight).toBe(300);
  });

  it("does nothing when the page already fits", async () => {
    const win = fakeWindow(0);
    const fit = windowFitter(win.setSize, win.view, 20);
    await fit(520, 600);
    expect(win.sizes).toEqual([]);
  });
});
