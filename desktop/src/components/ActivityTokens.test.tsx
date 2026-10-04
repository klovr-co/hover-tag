import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { ActivityTokens } from "./ActivityTokens";

afterEach(cleanup);
it("shows reported subsets on hover without adding them to the total, and dismisses with Escape", () => {
  render(<ActivityTokens usage={{ input_tokens: 1000, output_tokens: 200, total_tokens: 1200,
    cache_read_input_tokens: 700, cache_creation_input_tokens: 100, reasoning_output_tokens: 150 }} />);
  const trigger = screen.getByRole("button", { name: "1,200 tokens, show breakdown" });
  expect(screen.queryByRole("tooltip")).toBeNull();
  fireEvent.mouseEnter(trigger);
  const panel = within(screen.getByRole("tooltip"));
  for (const label of ["Input", "Output", "Cached input read", "Cache written", "Reasoning", "Total"]) {
    expect(panel.getByText(label)).toBeTruthy();
  }
  for (const count of ["1,000", "200", "700", "100", "150", "1,200"]) expect(panel.getByText(count)).toBeTruthy();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("tooltip")).toBeNull();
});

it("supports keyboard focus and leaves unreported details absent", () => {
  render(<ActivityTokens usage={{ input_tokens: 10, output_tokens: 5, total_tokens: 15 }} />);
  fireEvent.focus(screen.getByRole("button"));
  const tooltip = screen.getByRole("tooltip");
  expect(screen.getByRole("button").getAttribute("aria-describedby")).toBe(tooltip.id);
  expect(within(tooltip).queryByText("Reasoning")).toBeNull();
  expect(within(tooltip).queryByText("Cached input read")).toBeNull();
});
