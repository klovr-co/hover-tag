import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Avatar, Sky, tagIcon } from "./ui";

const startDragging = vi.fn(() => Promise.resolve());
vi.mock("@tauri-apps/api/window", () => ({ getCurrentWindow: () => ({ startDragging }) }));
vi.mock("@tauri-apps/api/core", () => ({ convertFileSrc: (path: string) => `asset://localhost${path}` }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Slack avatar", () => {
  it("loads a newly synced picture after falling back from a failed image", () => {
    vi.stubGlobal("__TAURI_INTERNALS__", {});
    const row = { id: "tag", valid: true, state: "running", avatar: "/old.png" };
    const { container, rerender } = render(<Avatar row={row} />);
    const image = () => container.querySelector("img")!;
    expect(image().getAttribute("src")).toBe("asset://localhost/old.png");
    fireEvent.error(image());
    expect(image().getAttribute("src")).toBe(tagIcon);
    rerender(<Avatar row={{ ...row, avatar: "/new.png" }} />);
    expect(image().getAttribute("src")).toBe("asset://localhost/new.png");
    expect(image().style.filter).toBe("");
  });

  it("replaces the placeholder once an existing Tag has been synced", () => {
    vi.stubGlobal("__TAURI_INTERNALS__", {});
    const row = { id: "tag", valid: true, state: "running", avatar: null };
    const { container, rerender } = render(<Avatar row={row} />);
    expect(container.querySelector("img")!.getAttribute("src")).toBe(tagIcon);
    rerender(<Avatar row={{ ...row, avatar: "/slack.png" }} />);
    expect(container.querySelector("img")!.getAttribute("src")).toBe("asset://localhost/slack.png");
  });
});

describe("Window headers", () => {
  it("drag the window from their text but not from their buttons", async () => {
    vi.stubGlobal("__TAURI_INTERNALS__", {});
    const { getByRole } = render(<Sky><h1>Your Tags</h1><button>Add Tag</button></Sky>);
    fireEvent.mouseDown(getByRole("button", { name: "Add Tag" }));
    fireEvent.mouseDown(getByRole("heading", { name: "Your Tags" }));
    await vi.waitFor(() => expect(startDragging).toHaveBeenCalledTimes(1));
  });
});
