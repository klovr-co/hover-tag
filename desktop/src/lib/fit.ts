// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Sizes the window to the page. Requests come in bursts while a screen loads, so
// they run one at a time and only the latest counts. The title bar may or may
// not be part of the size the window takes; that difference is learned once
// from what the page really got and reused, instead of measured mid-resize.

export const MIN_HEIGHT = 300;
export const MAX_HEIGHT = 860;

export interface View {
  readonly innerWidth: number;
  readonly innerHeight: number;
  addEventListener(type: "resize", listener: () => void): void;
  removeEventListener(type: "resize", listener: () => void): void;
}

/** Resolves after the page has been resized, or after `ms` when nothing changes. */
function resized(view: View, ms: number) {
  return new Promise<void>((resolve) => {
    const done = () => { view.removeEventListener("resize", done); clearTimeout(timer); resolve(); };
    const timer = setTimeout(done, ms);
    view.addEventListener("resize", done);
  });
}

export function windowFitter(setSize: (width: number, height: number) => Promise<void>, view: View, settle = 250) {
  let offset = 0;
  let wanted = { width: 0, height: 0 };
  let queue = Promise.resolve();
  const apply = async () => {
    const { width, height } = wanted;
    if (Math.abs(view.innerWidth - width) <= 1 && Math.abs(view.innerHeight - height) <= 1) return;
    let wait = resized(view, settle);
    await setSize(width, height + offset);
    await wait;
    const extra = view.innerHeight - height;
    if (Math.abs(extra) <= 1) return;
    offset -= extra;
    wait = resized(view, settle);
    await setSize(width, height + offset);
    await wait;
  };
  return (width: number, height: number) => {
    wanted = { width, height: Math.min(Math.max(height, MIN_HEIGHT), MAX_HEIGHT) };
    queue = queue.then(apply).catch(() => {});
    return queue;
  };
}
