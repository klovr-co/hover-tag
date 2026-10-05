// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// The order people drag workspaces and Tags into on Home. A display choice for this
// computer only; Tags never move between workspaces.
import { useCallback, useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import type { Group } from "./protocol";

export interface Order {
  workspaces: string[];
  tags: Record<string, string[]>;
}

const KEY = "tag.home-order";

export function savedOrder(): Order {
  try {
    const value = JSON.parse(window.localStorage.getItem(KEY) ?? "null");
    if (value && Array.isArray(value.workspaces) && value.tags && typeof value.tags === "object") return value;
  } catch { /* fall through to the natural order */ }
  return { workspaces: [], tags: {} };
}

function save(order: Order) {
  try { window.localStorage.setItem(KEY, JSON.stringify(order)); } catch { /* applies until the app closes */ }
}

/** Known items in their saved place; new ones keep their natural order after them. */
export function arrange<T>(items: T[], saved: string[] | undefined, id: (item: T) => string): T[] {
  const rank = new Map((saved ?? []).map((key, index) => [key, index]));
  return items
    .map((item, index) => ({ item, index, at: rank.get(id(item)) ?? Infinity }))
    .sort((a, b) => a.at - b.at || a.index - b.index)
    .map(({ item }) => item);
}

export function ordered(groups: Group[], order: Order): Group[] {
  return arrange(groups, order.workspaces, (g) => g.key)
    .map((g) => ({ ...g, rows: arrange(g.rows, order.tags[g.key], (row) => row.id) }));
}

export function move(ids: string[], from: number, to: number): string[] {
  const next = [...ids];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

export function useOrder() {
  const [order, setOrder] = useState(savedOrder);
  const update = useCallback((change: (order: Order) => Order) => {
    setOrder((current) => { const next = change(current); save(next); return next; });
  }, []);
  return {
    order,
    setWorkspaces: (ids: string[]) => update((o) => ({ ...o, workspaces: ids })),
    setTags: (workspace: string, ids: string[]) => update((o) => ({ ...o, tags: { ...o.tags, [workspace]: ids } })),
  };
}

const SLIDE = { duration: 160, easing: "cubic-bezier(.2, .8, .2, 1)" };
const calm = () => typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Slide an element from `dy` pixels away back to where layout put it. */
function settle(el: HTMLElement, dy: number) {
  if (!dy || calm() || typeof el.animate !== "function") return;
  el.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }], SLIDE);
}

/**
 * Drag to reorder a vertical list with the pointer, or Alt+Up/Down from the keyboard.
 * Pointer events, not HTML drag and drop, because the app window takes native drops.
 * A press only becomes a drag after a few pixels, so clicks still open things. The
 * lifted item follows the pointer, the others slide aside, and it settles on release.
 */
export function useReorder(ids: string[], commit: (ids: string[]) => void) {
  const [dragging, setDragging] = useState<string | null>(null);
  const items = useRef(new Map<string, HTMLElement>());
  const press = useRef<{ id: string; y: number; top: number; moved: boolean } | null>(null);
  const tops = useRef(new Map<string, number>());
  const swallowClick = useRef(false);
  const latest = useRef(ids);
  if (!press.current?.moved) latest.current = ids;

  const follow = (y: number) => {
    const p = press.current;
    const el = p && items.current.get(p.id);
    if (p && el) el.style.transform = `translateY(${y - p.y - (el.offsetTop - p.top)}px)`;
  };

  // After a live reorder, slide the others from where they were to their new place.
  useLayoutEffect(() => {
    const before = tops.current;
    tops.current = new Map([...items.current].map(([id, el]) => [id, el.offsetTop]));
    if (!press.current?.moved) return;
    for (const [id, el] of items.current) {
      if (id !== press.current.id && before.has(id)) settle(el, before.get(id)! - el.offsetTop);
    }
  });

  const onPointerMove = (event: PointerEvent) => {
    const p = press.current;
    if (!p) return;
    if (!p.moved && Math.abs(event.clientY - p.y) < 5) return;
    if (!p.moved) { p.moved = true; setDragging(p.id); }
    follow(event.clientY);
    const current = latest.current;
    const from = current.indexOf(p.id);
    const dragged = items.current.get(p.id)!.getBoundingClientRect();
    const middle = dragged.top + dragged.height / 2;
    let to = from;
    current.forEach((other, index) => {
      const box = other !== p.id && items.current.get(other)?.getBoundingClientRect();
      if (!box) return;
      if (index > from && middle > box.top + box.height / 2) to = Math.max(to, index);
      if (index < from && middle < box.top + box.height / 2) to = Math.min(to, index);
    });
    if (to !== from) {
      latest.current = move(current, from, to);
      commit(latest.current);
      requestAnimationFrame(() => follow(event.clientY));
    }
  };

  const end = () => {
    const p = press.current;
    const el = p && items.current.get(p.id);
    if (p?.moved && el) {
      swallowClick.current = true;
      const dy = new DOMMatrixReadOnly(getComputedStyle(el).transform).m42;
      el.style.transform = "";
      settle(el, dy);
    }
    press.current = null;
    setDragging(null);
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", end);
    window.removeEventListener("pointercancel", end);
  };

  const props = (id: string) => ({
    ref: (el: HTMLElement | null) => { if (el) items.current.set(id, el); else items.current.delete(id); },
    "data-dragging": dragging === id || undefined,
    onPointerDown: (event: ReactPointerEvent) => {
      if (ids.length < 2 || event.button !== 0 || (event.target as HTMLElement).closest("button:not(.r), input, a")) return;
      event.stopPropagation();
      press.current = { id, y: event.clientY, top: items.current.get(id)?.offsetTop ?? 0, moved: false };
      window.addEventListener("pointermove", onPointerMove);
      window.addEventListener("pointerup", end);
      window.addEventListener("pointercancel", end);
    },
    onClickCapture: (event: React.MouseEvent) => {
      if (swallowClick.current) { swallowClick.current = false; event.stopPropagation(); event.preventDefault(); }
    },
    onKeyDown: (event: React.KeyboardEvent) => {
      if (!event.altKey || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) return;
      const from = ids.indexOf(id);
      const to = from + (event.key === "ArrowUp" ? -1 : 1);
      if (to < 0 || to >= ids.length) return;
      event.preventDefault();
      event.stopPropagation();
      press.current = null;
      commit(move(ids, from, to));
    },
  });
  return { props, dragging: dragging !== null };
}
