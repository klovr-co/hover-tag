import { StrictMode, useState } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useReorder } from "./order";

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("PointerEvent", MouseEvent);
  vi.stubGlobal("DOMMatrixReadOnly", class { m42 = 0; });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function List({ ids = ["a", "b"], open, commit }: {
  ids?: string[]; open: (id: string) => void; commit: (ids: string[]) => void;
}) {
  const reorder = useReorder(ids, commit);
  return <div>{ids.map((id) => <div key={id} {...reorder.props(id)} data-testid={id} onClick={() => open(id)}>{id}</div>)}</div>;
}

function drag() {
  const item = screen.getByTestId("a");
  fireEvent.pointerDown(item, { button: 0, clientY: 0 });
  fireEvent.pointerMove(window, { clientY: 10 });
  expect(item.dataset.dragging).toBe("true");
  return item;
}

it("cancels a drag when its item disappears, then accepts a new drag", () => {
  const open = vi.fn();
  const commit = vi.fn();
  const view = render(<List open={open} commit={commit} />);
  drag();
  view.rerender(<List ids={["b", "c"]} open={open} commit={commit} />);
  fireEvent.pointerMove(window, { clientY: 20 });
  fireEvent.pointerUp(window);
  expect(commit).not.toHaveBeenCalled();
  fireEvent.click(screen.getByTestId("b"));
  expect(open).toHaveBeenCalledWith("b");
  fireEvent.pointerDown(screen.getByTestId("b"), { button: 0, clientY: 0 });
  fireEvent.pointerMove(window, { clientY: 10 });
  expect(screen.getByTestId("b").dataset.dragging).toBe("true");
});

it("removes the original pointer listeners on unmount after rerendering", () => {
  const add = vi.spyOn(window, "addEventListener");
  const remove = vi.spyOn(window, "removeEventListener");
  const view = render(<StrictMode><List open={vi.fn()} commit={vi.fn()} /></StrictMode>);
  drag();
  const listeners = add.mock.calls.filter(([type]) => ["pointermove", "pointerup", "pointercancel"].includes(type));
  expect(listeners).toHaveLength(3);
  view.unmount();
  for (const [type, listener] of listeners) expect(remove).toHaveBeenCalledWith(type, listener);
});

it("lets the next click through after pointer cancellation", () => {
  const open = vi.fn();
  render(<List open={open} commit={vi.fn()} />);
  const item = drag();
  fireEvent.pointerCancel(window);
  expect(item.style.transform).toBe("");
  expect(item.dataset.dragging).toBeUndefined();
  fireEvent.click(item);
  expect(open).toHaveBeenCalledWith("a");
});

it("suppresses the completed drag's click and lets later clicks through", () => {
  const open = vi.fn();
  render(<List open={open} commit={vi.fn()} />);
  const item = drag();
  fireEvent.pointerUp(item);
  fireEvent.click(item);
  expect(open).not.toHaveBeenCalled();
  fireEvent.click(item);
  expect(open).toHaveBeenCalledWith("a");
});

it("expires suppression when the release click lands outside the row", () => {
  const open = vi.fn();
  render(<List open={open} commit={vi.fn()} />);
  const item = drag();
  fireEvent.pointerUp(document.body);
  fireEvent.click(document.body);
  act(() => { vi.runOnlyPendingTimers(); });
  fireEvent.click(item);
  expect(open).toHaveBeenCalledWith("a");
});

it("does not suppress another row's click or a new pointer gesture", () => {
  const open = vi.fn();
  render(<List open={open} commit={vi.fn()} />);
  const item = drag();
  fireEvent.pointerUp(window);
  fireEvent.click(screen.getByTestId("b"));
  fireEvent.pointerDown(item, { button: 0, clientY: 10 });
  fireEvent.pointerUp(item);
  fireEvent.click(item);
  expect(open.mock.calls).toEqual([["b"], ["a"]]);
});

it("continues reordering across renders and releases the original listeners", () => {
  const commit = vi.fn();
  const open = vi.fn();
  function ReorderingList() {
    const [ids, setIds] = useState(["a", "b"]);
    return <List ids={ids} open={open} commit={(next) => { commit(next); setIds(next); }} />;
  }
  render(<ReorderingList />);
  const a = screen.getByTestId("a");
  const b = screen.getByTestId("b");
  vi.spyOn(a, "getBoundingClientRect").mockReturnValue({ top: 100, height: 20 } as DOMRect);
  vi.spyOn(b, "getBoundingClientRect").mockReturnValue({ top: 30, height: 20 } as DOMRect);
  drag();
  expect(commit).toHaveBeenCalledWith(["b", "a"]);
  expect(screen.getAllByTestId(/a|b/).map((el) => el.textContent)).toEqual(["b", "a"]);
  fireEvent.pointerUp(a);
  fireEvent.click(a);
  expect(open).not.toHaveBeenCalled();
  fireEvent.pointerMove(window, { clientY: 20 });
  expect(commit).toHaveBeenCalledTimes(1);
});
