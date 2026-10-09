// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Small helpers for animations CSS can't do alone: noticing a change after the first
// render, keeping something on screen while it animates out, and sliding a selection
// indicator between choices. With reduced motion, nothing waits or slides.
import { useEffect, useRef, useState } from "react";

/** Reduced motion, or no way to ask (tests): either way, don't wait on animations. */
export const reducedMotion = () =>
  typeof matchMedia !== "function" || matchMedia("(prefers-reduced-motion: reduce)").matches;

/** True for `ms` after `value` changes; never for the first value, so opening a screen stays calm. */
export function useFresh(value: unknown, ms = 900) {
  const first = useRef(value);
  const [fresh, setFresh] = useState(false);
  useEffect(() => {
    if (Object.is(first.current, value)) return;
    first.current = value;
    setFresh(true);
    const timer = setTimeout(() => setFresh(false), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return fresh;
}

/** Keeps the last non-null value for `ms` after it goes away, flagged as leaving. */
export function usePresence<T>(value: T | null, ms = 180) {
  const [shown, setShown] = useState(value);
  const [leaving, setLeaving] = useState(false);
  useEffect(() => {
    if (value !== null) { setShown(value); setLeaving(false); return; }
    if (reducedMotion()) { setShown(null); return; }
    setLeaving(true);
    const timer = setTimeout(() => { setShown(null); setLeaving(false); }, ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return { shown: value ?? shown, leaving: value === null && leaving };
}

export type Listed<T> = { item: T; state: "stay" | "enter" | "leave" };

/** A list where items added after the first render enter, and removed ones stay briefly to leave. */
export function useListMotion<T>(items: T[], key: (item: T) => string, ms = 220): Listed<T>[] {
  const known = useRef<Set<string> | null>(null);
  const last = useRef<T[]>(items);
  const [leaving, setLeaving] = useState<{ item: T; at: number }[]>([]);
  // Removal timers outlive list changes, so a reorder cannot strand a leaving row.
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());
  useEffect(() => () => { for (const timer of timers.current) clearTimeout(timer); }, []);
  if (known.current === null) known.current = new Set(items.map(key));
  // Callers often rebuild the array each render; only a change in which items are listed counts.
  const ids = items.map(key).join("\n");
  useEffect(() => {
    const now = new Set(items.map(key));
    const gone = last.current.flatMap((item, at) => now.has(key(item)) ? [] : [{ item, at }]);
    last.current = items;
    if (!gone.length || reducedMotion()) return;
    setLeaving((all) => [...all.filter((g) => !now.has(key(g.item))), ...gone]);
    const timer = setTimeout(() => {
      timers.current.delete(timer);
      setLeaving((all) => all.filter((g) => !gone.some((x) => key(x.item) === key(g.item))));
    }, ms);
    timers.current.add(timer);
    // `key` is a pure accessor and `ids` stands for `items`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids, ms]);
  // New items stay "enter" for the length of their animation, even if something else re-renders the list.
  useEffect(() => {
    const fresh = items.map(key).filter((k) => !known.current!.has(k));
    if (!fresh.length) return;
    const timer = setTimeout(() => { for (const k of fresh) known.current!.add(k); }, ms);
    return () => { clearTimeout(timer); for (const k of fresh) known.current!.add(k); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids, ms]);
  const out: Listed<T>[] = items.map((item) => ({ item, state: known.current!.has(key(item)) ? "stay" : "enter" }));
  for (const { item, at } of leaving) {
    if (!items.some((i) => key(i) === key(item))) out.splice(Math.min(at, out.length), 0, { item, state: "leave" });
  }
  return out;
}

const GLIDERS = ".segc, .sl-tabs";
const PICKED = ':scope > [aria-checked="true"], :scope > [aria-selected="true"]';

/** Place each segmented control's and tab bar's indicator under its selected choice. */
function place(root: ParentNode) {
  for (const bar of root.querySelectorAll<HTMLElement>(GLIDERS)) {
    const picked = bar.querySelector<HTMLElement>(PICKED);
    if (!picked) { bar.classList.remove("glide"); continue; }
    const x = `${picked.offsetLeft}px`, w = `${picked.offsetWidth}px`;
    const y = `${picked.offsetTop}px`, h = `${picked.offsetHeight}px`;
    if (bar.style.getPropertyValue("--gx") === x && bar.style.getPropertyValue("--gw") === w && bar.style.getPropertyValue("--gy") === y) continue;
    bar.style.setProperty("--gx", x); bar.style.setProperty("--gw", w);
    bar.style.setProperty("--gy", y); bar.style.setProperty("--gh", h);
    if (!bar.classList.contains("glide")) {
      bar.classList.add("glide");
      // The first placement jumps; only later changes slide.
      requestAnimationFrame(() => requestAnimationFrame(() => bar.classList.add("gliding")));
    }
  }
}

/** Watches the window for selection changes; returns a function that stops watching. */
export function glide(root: HTMLElement) {
  let frame = 0;
  const later = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(() => place(root)); };
  const watch = new MutationObserver(later);
  watch.observe(root, { subtree: true, childList: true, attributes: true, attributeFilter: ["aria-checked", "aria-selected"] });
  window.addEventListener("resize", later);
  later();
  return () => { cancelAnimationFrame(frame); watch.disconnect(); window.removeEventListener("resize", later); };
}
