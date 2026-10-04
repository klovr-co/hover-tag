import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { demoBridge } from "../lib/bridge";
import type { ActivityItem } from "../lib/home";
import { ActivityDetails, StepsToggle } from "./ActivityDetails";

afterEach(cleanup);
const item: ActivityItem = { run_id: "a".repeat(32), at: new Date().toISOString(), kind: "failed", channel: "C1", channel_name: "launch", dm: false };
const detail = { run_id: item.run_id, outcome: "failed", started_at: item.at, finished_at: item.at, team: "T1", channel: "C1", thread_ts: "1.0",
  events: [{ label: "Reading a file…", status: "failed", started_at: item.at, details: { tool: "cat plan.md", input: "plan.md", output: "File missing" } }],
  omitted: 2, error: { reference: "AAAABBBB", text: "Cause: File missing" } };
describe("Activity details", () => {
  it("loads on demand, shows recorded errors and steps, and reuses the result", async () => {
    const api = demoBridge();
    api.tag = vi.fn(async () => ({ code: 0, stdout: JSON.stringify({ ok: true, activity: detail }), stderr: "" }));
    api.open = vi.fn();
    render(<ActivityDetails api={api} tag="tag1" item={item} />);
    expect(api.tag).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "View details" }));
    expect(await screen.findByText("Cause: File missing")).toBeTruthy();
    expect(screen.getByText("cat plan.md")).toBeTruthy();
    expect(screen.getByText("2 later steps omitted.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Open thread in Slack" }));
    expect(api.open).toHaveBeenCalledWith("slack://channel?team=T1&id=C1&message=1.0");
    fireEvent.click(screen.getByRole("button", { name: "Hide details" }));
    fireEvent.click(screen.getByRole("button", { name: "View details" }));
    expect(api.tag).toHaveBeenCalledTimes(1);
  });
  it("shows unavailable records and lets the user retry", async () => {
    const api = demoBridge();
    api.tag = vi.fn().mockResolvedValueOnce({ code: 1, stdout: JSON.stringify({ ok: false, error: "Activity expired." }), stderr: "" })
      .mockResolvedValue({ code: 0, stdout: JSON.stringify({ ok: true, activity: { ...detail, events: [], error: null } }), stderr: "" });
    render(<ActivityDetails api={api} tag="tag1" item={item} />);
    fireEvent.click(screen.getByRole("button", { name: "View details" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    fireEvent.click(screen.getByText("Try again"));
    expect(await screen.findByText("No tool activity was recorded for this request.")).toBeTruthy();
    expect(screen.getByText("No matching error report is available for this request.")).toBeTruthy();
  });
  it("refreshes a running run when the feed reports completion", async () => {
    const api = demoBridge();
    api.tag = vi.fn().mockResolvedValueOnce({ code: 0, stdout: JSON.stringify({ ok: true, activity: { ...detail, outcome: "running", error: null } }), stderr: "" })
      .mockResolvedValue({ code: 0, stdout: JSON.stringify({ ok: true, activity: detail }), stderr: "" });
    const view = render(<ActivityDetails api={api} tag="tag1" item={{ ...item, kind: "working" }} />);
    fireEvent.click(screen.getByRole("button", { name: "View details" }));
    await screen.findByText("Refresh details");
    view.rerender(<ActivityDetails api={api} tag="tag1" item={item} />);
    expect(await screen.findByText("Cause: File missing")).toBeTruthy();
    expect(api.tag).toHaveBeenCalledTimes(2);
  });
});


it("omits unrecorded tool previews and empty expansion controls", async () => {
  const api = demoBridge();
  api.tag = vi.fn(async () => ({ code: 0, stderr: "", stdout: JSON.stringify({ ok: true, activity: {
    ...detail, events: [
      { ...detail.events[0], label: "No preview", details: {} },
      { ...detail.events[0], label: "Input only", details: { input: "plan.md", output: " " } },
    ],
  } }) }));
  render(<ActivityDetails api={api} tag="tag1" item={item} />);
  fireEvent.click(screen.getByRole("button", { name: "View details" }));
  const emptyStep = await screen.findByText("No preview");
  expect(emptyStep.closest("details.activity-step")).toBeNull();
  expect(screen.getByText("plan.md")).toBeTruthy();
  expect(screen.queryByText("Result")).toBeNull();
  expect(screen.queryByText("Not recorded.")).toBeNull();
  expect(screen.getAllByText("Saved preview · shortened and redacted")).toHaveLength(1);
});

describe("Steps toggle", () => {
  const toggle = (overrides: Partial<ActivityItem>) => render(<StepsToggle item={{ ...item, ...overrides }} open={false} controls="x" onToggle={() => {}} />);
  it("counts steps and flags failed or running work", () => {
    expect(toggle({ kind: "replied", step_count: 1 }).container.textContent).toBe("· 1 step");
    cleanup();
    expect(toggle({ kind: "failed", step_count: 3 }).container.textContent).toBe("· Failed · 3 steps");
    cleanup();
    expect(toggle({ kind: "working" }).container.textContent).toBe("· Working");
  });
  it("hides when there is no work to show", () => {
    expect(toggle({ kind: "replied" }).container.textContent).toBe("");
    cleanup();
    expect(toggle({ kind: "failed", run_id: undefined, step_count: 2 }).container.textContent).toBe("");
  });
});
