// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Markdown } from "./markdown";

afterEach(cleanup);

const html = (text: string, open = vi.fn()) => render(<Markdown text={text} open={open} />).container.firstElementChild!;

describe("Markdown", () => {
  it("renders an answer's headings, emphasis, lists, tables and code", () => {
    const root = html([
      "## Launch checklist", "", "**Owners** are set; _dates_ are ~~tentative~~ confirmed.", "",
      "- [x] Draft notes", "- [ ] Send invites", "  - to the beta list", "",
      "1. First", "2. Second", "",
      "| Task | Owner |", "| --- | ---: |", "| Comms | Sam |", "",
      "```", "tag start", "```", "> quoted", "", "---", "use `snake_case_names` here",
    ].join("\n"));
    expect(root.querySelector("h4")?.textContent).toBe("Launch checklist");
    expect(root.querySelector("strong")?.textContent).toBe("Owners");
    expect(root.querySelector("em")?.textContent).toBe("dates");
    expect(root.querySelector("del")?.textContent).toBe("tentative");
    expect([...root.querySelectorAll("li.md-task")].map((li) => li.classList.contains("done"))).toEqual([true, false]);
    expect(root.querySelector("li.md-task ul li")?.textContent).toBe("to the beta list");
    expect([...root.querySelectorAll("ol > li")].map((li) => li.textContent)).toEqual(["First", "Second"]);
    expect([...root.querySelectorAll("td")].map((td) => td.textContent)).toEqual(["Comms", "Sam"]);
    expect((root.querySelectorAll("td")[1] as HTMLElement).style.textAlign).toBe("right");
    expect(root.querySelector("pre code")?.textContent).toBe("tag start");
    expect(root.querySelector("blockquote")?.textContent).toBe("quoted");
    expect(root.querySelector("hr")).toBeTruthy();
    expect(root.querySelector("p:last-child code")?.textContent).toBe("snake_case_names");
  });

  it("keeps a message's line breaks and leaves words with underscores alone", () => {
    const root = html("first line\nsecond line with some_file_name.md");
    expect(root.querySelectorAll("br")).toHaveLength(1);
    expect(root.querySelector("em")).toBeNull();
    expect(root.textContent).toContain("some_file_name.md");
  });

  it("opens safe links through the app and never makes markup from text", () => {
    const open = vi.fn();
    const root = html("[Plan](https://example.com/plan) and https://example.com/x. <script>alert(1)</script> [bad](javascript:alert(1))", open);
    const links = root.querySelectorAll("a");
    expect([...links].map((a) => a.getAttribute("href"))).toEqual(["https://example.com/plan", "https://example.com/x"]);
    fireEvent.click(links[0]);
    expect(open).toHaveBeenCalledWith("https://example.com/plan");
    expect(root.querySelector("script")).toBeNull();
    expect(root.textContent).toContain("<script>alert(1)</script>");
    expect(root.textContent).toContain("bad");
  });
});
