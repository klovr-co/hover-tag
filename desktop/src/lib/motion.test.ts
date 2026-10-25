import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useFresh, useListMotion, usePresence } from "./motion";

const motion = (reduce: boolean) => vi.stubGlobal("matchMedia", (query: string) => ({ matches: reduce && query.includes("reduce") }));
beforeEach(() => { vi.useFakeTimers(); motion(false); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

it("is fresh only after the value changes, then settles", () => {
  const { result, rerender } = renderHook(({ v }) => useFresh(v, 500), { initialProps: { v: "off" } });
  expect(result.current).toBe(false);
  rerender({ v: "on" });
  expect(result.current).toBe(true);
  act(() => { vi.advanceTimersByTime(500); });
  expect(result.current).toBe(false);
});

it("keeps a removed value while it leaves, unless motion is reduced", () => {
  const { result, rerender } = renderHook(({ v }) => usePresence<string>(v, 180), { initialProps: { v: "Copied" as string | null } });
  rerender({ v: null });
  expect(result.current).toEqual({ shown: "Copied", leaving: true });
  act(() => { vi.advanceTimersByTime(180); });
  expect(result.current).toEqual({ shown: null, leaving: false });
  motion(true);
  rerender({ v: "Saved" });
  rerender({ v: null });
  expect(result.current.shown).toBeNull();
});

it("marks added items as entering and keeps removed ones in place while they leave", () => {
  const key = (s: string) => s;
  const { result, rerender } = renderHook(({ items }) => useListMotion(items, key, 220), { initialProps: { items: ["a", "b", "c"] } });
  expect(result.current.map((x) => x.state)).toEqual(["stay", "stay", "stay"]);
  rerender({ items: ["a", "c", "d"] });
  expect(result.current).toEqual([
    { item: "a", state: "stay" }, { item: "b", state: "leave" }, { item: "c", state: "stay" }, { item: "d", state: "enter" },
  ]);
  act(() => { vi.advanceTimersByTime(220); });
  expect(result.current.map((x) => `${x.item}:${x.state}`)).toEqual(["a:stay", "c:stay", "d:stay"]);
});
