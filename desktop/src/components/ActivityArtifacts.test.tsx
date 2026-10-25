import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { demoBridge } from "../lib/bridge";
import type { ActivityItem } from "../lib/home";
import { ActivityArtifacts } from "./ActivityArtifacts";

afterEach(cleanup);
it("opens uploaded files in Slack, local files locally, and labels failed images without dead links", () => {
  const api = demoBridge();
  api.open = vi.fn().mockResolvedValue(undefined);
  const item: ActivityItem = { at: "", kind: "replied", channel: "C1", channel_name: "launch", dm: false,
    artifact_thread_url: "slack://channel?team=T1&id=C1&message=1.1",
    artifacts: [
      { name: "plan.md", kind: "file", delivery: "uploaded", url: "https://example.slack.com/files/F1/plan.md" },
      { name: "notes.txt", kind: "file", delivery: "local", local_path: "/work/notes.txt" },
      { name: "large.pdf", kind: "file", delivery: "upload_failed", local_path: "/work/large.pdf" },
      { name: "poster.png", kind: "image", delivery: "upload_failed" },
      { name: "chart.png", kind: "image", delivery: "uploaded" },
    ] };
  render(<ActivityArtifacts api={api} item={item} />);
  fireEvent.click(screen.getByRole("button", { name: "Open plan.md in Slack" }));
  fireEvent.click(screen.getByRole("button", { name: "Open notes.txt locally" }));
  fireEvent.click(screen.getByRole("button", { name: "Open large.pdf locally" }));
  fireEvent.click(screen.getByRole("button", { name: "Open chart.png in Slack" }));
  expect(api.open).toHaveBeenNthCalledWith(1, item.artifacts![0].url);
  expect(api.open).toHaveBeenNthCalledWith(2, "/work/notes.txt");
  expect(api.open).toHaveBeenNthCalledWith(3, "/work/large.pdf");
  expect(api.open).toHaveBeenNthCalledWith(4, item.artifact_thread_url);
  expect(screen.getAllByText("Upload failed")).toHaveLength(2);
  expect(screen.queryByRole("button", { name: /poster/ })).toBeNull();
});

it("reports a missing local file without losing the feed", async () => {
  const api = demoBridge();
  api.open = vi.fn().mockRejectedValue(new Error("missing"));
  render(<ActivityArtifacts api={api} item={{ at: "", kind: "replied", channel: "C1", channel_name: null, dm: false,
    artifacts: [{ name: "old.pdf", kind: "file", delivery: "local", local_path: "/work/old.pdf" }] }} />);
  fireEvent.click(screen.getByRole("button", { name: "Open old.pdf locally" }));
  expect((await screen.findByRole("alert")).textContent).toContain("may have moved");
});
