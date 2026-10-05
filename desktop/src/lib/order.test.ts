// The saved Home order: workspaces and Tags in their dragged place, new ones after.
import { afterEach, describe, expect, it } from "vitest";
import { act, renderHook } from "@testing-library/react";
import type { Group, TagRow } from "./protocol";
import { arrange, move, ordered, savedOrder, useOrder } from "./order";

afterEach(() => window.localStorage.clear());

const row = (id: string) => ({ id }) as TagRow;
const group = (key: string, ids: string[]): Group => ({ key, label: key, icon: null, rows: ids.map(row) });

describe("Home order", () => {
  it("puts saved items first and keeps new ones in their natural order", () => {
    expect(arrange(["a", "b", "c", "d"], ["c", "gone", "a"], (x) => x)).toEqual(["c", "a", "b", "d"]);
    expect(move(["a", "b", "c"], 0, 2)).toEqual(["b", "c", "a"]);
  });

  it("orders Tags only within their own workspace", () => {
    const result = ordered([group("A", ["a1", "a2"]), group("K", ["k1", "k2"])],
      { workspaces: ["K", "A"], tags: { K: ["k2", "k1"], A: ["k1", "a2"] } });
    expect(result.map((g) => [g.key, g.rows.map((r) => r.id)])).toEqual([["K", ["k2", "k1"]], ["A", ["a2", "a1"]]]);
  });

  it("remembers the order and ignores a damaged saved value", () => {
    const { result } = renderHook(() => useOrder());
    act(() => result.current.setWorkspaces(["K", "A"]));
    act(() => result.current.setTags("K", ["k2", "k1"]));
    expect(savedOrder()).toEqual({ workspaces: ["K", "A"], tags: { K: ["k2", "k1"] } });
    window.localStorage.setItem("tag.home-order", "{nope");
    expect(savedOrder()).toEqual({ workspaces: [], tags: {} });
  });

  it("keeps valid saved orders while discarding malformed entries in valid JSON", () => {
    window.localStorage.setItem("tag.home-order", JSON.stringify({
      workspaces: ["A", 42], tags: { K: "bad", A: ["a2", "a1"], B: null, C: ["c2", 42], D: {} },
    }));
    const saved = savedOrder();
    expect(saved).toEqual({ workspaces: ["A"], tags: { A: ["a2", "a1"] } });
    const result = ordered([group("K", ["k1", "k2"]), group("A", ["a1", "a2"]), group("C", ["c1", "c2"])], saved);
    expect(result.map((g) => [g.key, g.rows.map((r) => r.id)]))
      .toEqual([["A", ["a2", "a1"]], ["K", ["k1", "k2"]], ["C", ["c1", "c2"]]]);
  });
});
