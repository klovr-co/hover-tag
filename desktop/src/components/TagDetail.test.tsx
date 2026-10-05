// Tag detail: the feed shows only Tag's own records, and model changes wait for a click.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { type Bridge, demoBridge } from "../lib/bridge";
import { parseList, type TagRow } from "../lib/protocol";
import type { Tags } from "../lib/tags";
import { activityText, TagDetail } from "./TagDetail";

afterEach(cleanup);

async function open(tab?: string, canDescribe = false, configure?: (api: Bridge, rows: TagRow[]) => void) {
  const api = demoBridge();
  const rows: TagRow[] = parseList((await api.tag(["list", "--json"])).stdout);
  configure?.(api, rows);
  const calls: string[][] = [];
  const run = api.tag;
  api.tag = (args) => { calls.push(args); return run(args); };
  const remove = vi.fn(async () => true);
  const describe = vi.fn(async () => true);
  const tags = { rows, busy: new Set<string>(), toggle: vi.fn(), workspace: vi.fn(), rename: vi.fn(async () => true), describe, refresh: vi.fn(), remove } as unknown as Tags;
  const say = vi.fn();
  render(<TagDetail api={api} tags={tags} initial={rows[0].id} problems={{}} back={vi.fn()} add={vi.fn()} showSettings={vi.fn()}
    finishSetup={vi.fn()} openAI={vi.fn()} say={say} canDescribe={canDescribe} />);
  if (tab) fireEvent.click(screen.getByRole("tab", { name: tab }));
  return { calls, say, rows, remove, describe };
}

describe("Tag detail", () => {
  it("shows the Tag's recorded replies, and nothing it didn't do", async () => {
    await open();
    expect(await screen.findByText("#launch")).toBeTruthy();
    expect(screen.getByText("Created a launch checklist with owners and next steps.")).toBeTruthy();
    expect(screen.queryByText("I reviewed the launch notes and prepared the following checklist.")).toBeNull();
    expect(screen.getByText("Today")).toBeTruthy();
    expect(document.querySelector(".activity-reply-place")?.textContent).toBe("Replied in #launch");
    expect(screen.getByTitle("codex · gpt-5.5 · Medium thinking").textContent).toBe("· GPT-5.5 med");
    expect(screen.getByText("· 43s")).toBeTruthy();
    expect(screen.getByText("· 12.3K tokens")).toBeTruthy();
    fireEvent.mouseEnter(screen.getByRole("button", { name: "12,340 tokens, show breakdown" }));
    expect(screen.getByRole("tooltip").textContent).toContain("Cached input read");
    expect(screen.queryByText(/Connected to Slack|Memory ready/)).toBeNull();
    expect(activityText({ at: "", kind: "failed", channel: "D1", channel_name: null, dm: true })).toBe("Couldn't finish a request in a direct message");
  });

  it("lists the workspace's Tags and channels, and a channel shows its Tags", async () => {
    await open();
    expect(screen.getByRole("button", { name: /Research Tag/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^#\s*general$/ }));
    expect(screen.getByText("1 Tag answers here")).toBeTruthy();
  });

  it("asks before restarting a running Tag to use a new thinking level", async () => {
    const { calls } = await open("Details");
    expect(screen.queryByRole("button", { name: "Connections" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Change account" })).toBeNull();
    fireEvent.click(await screen.findByRole("radio", { name: "High" }, { timeout: 5000 }));
    expect(screen.getByText("Restart Maya's Tag to use GPT-5.5 · High thinking.")).toBeTruthy();
    expect(calls.some((args) => args.includes("model"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Save and restart" }));
    await vi.waitFor(() => expect(calls).toContainEqual([expect.any(String), "settings", "ai", "model", "codex:gpt-5.5", "--effort", "high", "--restart", "--json"]));
  });

  it("deletes a Slack app only after its exact App ID is typed, and removing keeps the app", async () => {
    const { remove, rows } = await open("Details");
    const row = rows[0];
    fireEvent.click(await screen.findByRole("button", { name: "Remove this Tag…" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(remove).toHaveBeenCalledWith(row);
    fireEvent.click(screen.getByRole("button", { name: "Also delete the Slack app…" }));
    const confirm = screen.getByRole("button", { name: "Delete app and remove" }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("App ID to confirm"), { target: { value: row.slack_app_id } });
    expect(confirm.disabled).toBe(false);
    fireEvent.click(confirm);
    expect(remove).toHaveBeenLastCalledWith(row, row.slack_app_id);
  });

  it("edits the description in place when Tag can change it in Slack", async () => {
    const { describe, rows, say } = await open("Details", true);
    expect(screen.getByText(rows[0].description!)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const field = screen.getByLabelText("Description") as HTMLTextAreaElement;
    expect(field.value).toBe(rows[0].description);
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    expect(document.querySelector(".desc-count")).toBeNull();
    fireEvent.change(field, { target: { value: "x".repeat(115) } });
    expect(document.querySelector(".desc-count")?.textContent).toBe("25");
    fireEvent.change(field, { target: { value: "Answers launch\nquestions" } });
    expect(field.value).toBe("Answers launch questions");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(describe).toHaveBeenCalledWith(rows[0], "Answers launch questions");
    await vi.waitFor(() => expect(say).toHaveBeenCalledWith("Saved the description"));
  });

  it("shows why a change failed instead of quietly leaving the form open", async () => {
    const api = demoBridge();
    const rows: TagRow[] = parseList((await api.tag(["list", "--json"])).stdout);
    const tags = { rows, busy: new Set<string>(), error: "Rename failed. Slack did not save the new name; retry `tag maya rename \"Maxine's Tag\"`",
      toggle: vi.fn(), workspace: vi.fn(), rename: vi.fn(async () => false), refresh: vi.fn(), remove: vi.fn() } as unknown as Tags;
    render(<TagDetail api={api} tags={tags} initial={rows[0].id} problems={{}} back={vi.fn()} add={vi.fn()} showSettings={vi.fn()}
      finishSetup={vi.fn()} openAI={vi.fn()} say={vi.fn()} />);
    fireEvent.click(screen.getByRole("tab", { name: "Details" }));
    expect(screen.getByText(/Slack did not save the new name/)).toBeTruthy();
  });

  it("shows the description but no Edit when the installed Tag can't change it", async () => {
    const { rows } = await open("Details");
    expect(screen.getByText(rows[0].description!)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  });

  it("Cancel puts the saved level back", async () => {
    await open("Details");
    await screen.findByRole("radio", { name: "Low" }, { timeout: 5000 });
    const radios = screen.getAllByRole("radio");
    const saved = radios.find((radio) => radio.getAttribute("aria-checked") === "true")!;
    fireEvent.click(radios.find((radio) => radio !== saved)!);
    expect(saved.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(saved.getAttribute("aria-checked")).toBe("true");
    expect(screen.queryByText(/Restart Maya's Tag/)).toBeNull();
  });
});

const entry = (summary: string, at: string, kind = "replied", channel = "C0LAUNCH1") => ({
  run_id: summary, reply_summary: summary, at, kind, channel, channel_name: "launch", dm: false, step_count: 2,
});

it("shows chronological history, hides failures by default, and remembers Show errors", async () => {
  const { calls } = await open(undefined, false, (api) => {
    const run = api.tag;
    api.tag = async (args) => args[1] === "logs" ? { code: 0, stderr: "", stdout: JSON.stringify({ services: {}, activity: [
      entry("Older reply", "2026-10-04T23:00:00Z"),
      entry("Newest reply", "2026-10-05T09:00:00+08:00"),
      entry("failure", "2026-10-05T00:30:00Z", "failed"),
      entry("stopped", "2026-10-05T00:00:00Z", "stopped"),
    ] }) } : run(args);
  });
  await screen.findByText("Newest reply");
  const messages = () => [...document.querySelectorAll(".sl-msg p")].map((el) => el.textContent);
  expect(messages()).toEqual(["Older reply", "Stopped a request in #launch", "Newest reply"]);
  fireEvent.click(screen.getByRole("checkbox", { name: "Show errors" }));
  await screen.findByText(/Couldn't finish a request in/);
  expect(messages()).toEqual(["Older reply", "Stopped a request in #launch", "Couldn't finish a request in #launch", "Newest reply"]);
  await vi.waitFor(() => expect(calls.some((args) => args.includes("--hide-errors"))).toBe(true));
  fireEvent.click(screen.getByRole("tab", { name: "Channels 2" }));
  fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
  expect((screen.getByRole("checkbox", { name: "Show errors" }) as HTMLInputElement).checked).toBe(true);
  fireEvent.click(screen.getByRole("checkbox", { name: "Show errors" }));
  await screen.findByText("Newest reply");
  expect(messages()).toHaveLength(3);
});

it("merges channel activity across workspace Tags, scopes requests, and opens the correct Tag's details", async () => {
  let secondTag = "";
  const { calls, rows } = await open(undefined, false, (api, rows) => {
    secondTag = rows[1].id;
    const run = api.tag;
    api.tag = async (args) => {
      if (args[1] !== "logs" || args.includes("--activity")) return run(args);
      return { code: 0, stderr: "", stdout: JSON.stringify({ services: {}, activity: args[0] === rows[0].id ? [
        entry("First Tag reply", "2026-10-05T01:00:00Z"),
        entry("Wrong channel", "2026-10-05T03:00:00Z", "replied", "C0GENERAL"),
      ] : [entry("Second Tag reply", "2026-10-05T02:00:00Z")] }) };
    };
  });
  fireEvent.click(screen.getByRole("button", { name: /^#\s*launch$/ }));
  await screen.findByText("Second Tag reply");
  expect(screen.getByRole("tab", { name: "Activity" }).getAttribute("aria-selected")).toBe("true");
  expect([...document.querySelectorAll(".activity-reply-preview")].map((el) => el.textContent)).toEqual(["First Tag reply", "Second Tag reply"]);
  expect(screen.queryByText("Wrong channel")).toBeNull();
  expect(document.querySelector(".activity-reply-place")).toBeNull();
  expect(screen.queryByText(/Replied in/)).toBeNull();
  expect(calls.filter((args) => args.includes("--activity-channel")).map((args) => args[0])).toEqual([rows[0].id, secondTag]);
  fireEvent.click(screen.getAllByRole("button", { name: /^2 steps/ })[1]);
  await vi.waitFor(() => expect(calls).toContainEqual([secondTag, "logs", "--activity", "Second Tag reply", "--json"]));
  fireEvent.click(screen.getByRole("tab", { name: "Tags in this channel" }));
  expect(screen.getAllByRole("button", { name: "View" })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: /^#\s*general$/ }));
  expect(screen.getByRole("tab", { name: "Activity" }).getAttribute("aria-selected")).toBe("true");
  expect(screen.queryByText("Second Tag reply")).toBeNull();
});

it("keeps available channel activity and reports a failed Tag read", async () => {
  await open(undefined, false, (api, rows) => {
    const run = api.tag;
    api.tag = async (args) => args[1] === "logs" && args[0] === rows[1].id
      ? { code: 1, stdout: "", stderr: "Unavailable" } : run(args);
  });
  fireEvent.click(screen.getByRole("button", { name: /^#\s*launch$/ }));
  expect(await screen.findByText(/Couldn't load activity for Research Tag/)).toBeTruthy();
  expect(screen.getByText("Created a launch checklist with owners and next steps.")).toBeTruthy();
});

it("opens at the latest request, loads older records on upward scroll, and preserves the reading position", async () => {
  const height = vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockImplementation(function (this: HTMLElement) {
    return this.classList.contains("sl-body") ? this.querySelectorAll(".sl-msg").length * 20 : 0;
  });
  const clientHeight = vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(200);
  try {
    let calls: string[][] = [];
    // Flush each mocked response and its effects before the next scroll/assertion.
    await act(async () => {
      ({ calls } = await open(undefined, false, (api) => {
        const run = api.tag;
        api.tag = async (args) => {
          if (args[1] !== "logs") return run(args);
          const limit = Number(args[args.indexOf("--activity-limit") + 1]);
          const all = Array.from({ length: 120 }, (_, index) => entry(`Reply ${index}`, new Date(Date.UTC(2026, 9, 5, 0, index)).toISOString())).reverse();
          return { code: 0, stderr: "", stdout: JSON.stringify({ services: {}, activity: all.slice(0, limit), activity_has_more: limit < all.length }) };
        };
      }));
    });
    expect(screen.getByText("Reply 119")).toBeTruthy();
    const panel = screen.getByRole("tabpanel");
    expect(panel.scrollTop).toBe(1000);
    expect(screen.queryByText("Reply 69")).toBeNull();
    panel.scrollTop = 5;
    await act(async () => { fireEvent.scroll(panel); });
    expect(screen.getByText("Reply 20")).toBeTruthy();
    expect(panel.scrollTop).toBe(1005);
    expect(calls.some((args) => args.includes("--activity-limit") && args[args.indexOf("--activity-limit") + 1] === "100")).toBe(true);
    expect([...document.querySelectorAll(".activity-reply-preview")].map((el) => el.textContent).slice(-1)).toEqual(["Reply 119"]);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Load older activity" })); });
    expect(screen.getByText("Reply 0")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Load older activity" })).toBeNull();
    expect(panel.scrollTop).toBe(1405);
  } finally { height.mockRestore(); clientHeight.mockRestore(); }
});

it("loads channel history as a continuous chronological window across Tags", async () => {
  await open(undefined, false, (api, rows) => {
    const run = api.tag;
    api.tag = async (args) => {
      if (args[1] !== "logs") return run(args);
      const limit = Number(args[args.indexOf("--activity-limit") + 1]);
      const all = Array.from({ length: 60 }, (_, index) => {
        const minute = index * 2 + (args[0] === rows[0].id ? 0 : 1);
        return entry(`Channel reply ${minute}`, new Date(Date.UTC(2026, 9, 5, 0, minute)).toISOString());
      }).reverse();
      return { code: 0, stderr: "", stdout: JSON.stringify({ services: {}, activity: all.slice(0, limit), activity_has_more: limit < all.length }) };
    };
  });
  fireEvent.click(screen.getByRole("button", { name: /^#\s*launch$/ }));
  await screen.findByText("Channel reply 119");
  const summaries = () => [...document.querySelectorAll(".activity-reply-preview")].map((el) => el.textContent);
  expect(summaries()).toEqual(Array.from({ length: 50 }, (_, index) => `Channel reply ${index + 70}`));
  fireEvent.click(screen.getByRole("button", { name: "Load older activity" }));
  await screen.findByText("Channel reply 20");
  expect(summaries()).toEqual(Array.from({ length: 100 }, (_, index) => `Channel reply ${index + 20}`));
  fireEvent.click(screen.getByRole("button", { name: "Load older activity" }));
  await screen.findByText("Channel reply 0");
  expect(summaries()).toHaveLength(120);
  expect(screen.queryByRole("button", { name: "Load older activity" })).toBeNull();
});


it("hides empty reply rows and uses a plain empty state in Tag and channel activity", async () => {
  await open(undefined, false, (api) => {
    const run = api.tag;
    api.tag = async (args) => args.includes("logs") ? { code: 0, stderr: "", stdout: JSON.stringify({
      services: {}, activity: [
        { ...entry("legacy", new Date().toISOString()), reply_summary: " ", reply_preview: "", duration_seconds: 5 },
        { ...entry("missing", new Date().toISOString()), reply_summary: null, reply_preview: null, duration_seconds: 14 },
      ], activity_has_more: false,
    }) } : run(args);
  });
  await screen.findByText("No activity yet");
  expect(document.querySelector(".sl-msg")).toBeNull();
  expect(screen.queryByRole("button", { name: "View details" })).toBeNull();
  const filter = screen.getByRole("checkbox", { name: "Show errors" });
  expect(filter.closest(".sl-tabbar")).toBeTruthy();
  expect(filter.closest('[role="tabpanel"]')).toBeNull();
  fireEvent.click(filter);
  await screen.findByText("No activity yet");
  fireEvent.click(screen.getByRole("button", { name: /^#\s*launch$/ }));
  await screen.findByText("No activity in #launch yet");
  expect(document.querySelector(".sl-msg")).toBeNull();
  expect(screen.queryByText(/errors hidden|errors shown/)).toBeNull();
});

it("retains excerpts and artifact-only replies without unavailable metadata", async () => {
  await open(undefined, false, (api) => {
    const run = api.tag;
    api.tag = async (args) => args.includes("logs") ? { code: 0, stderr: "", stdout: JSON.stringify({
      services: {}, activity: [
        { ...entry("summary", new Date().toISOString()), duration_seconds: 0 },
        { ...entry("excerpt", new Date().toISOString()), reply_summary: " ", reply_preview: "Saved reply excerpt", duration_seconds: -1 },
        { ...entry("file", new Date().toISOString()), reply_summary: null,
          artifacts: [{ name: "notes.md", kind: "file", delivery: "local" }] },
      ], activity_has_more: false,
    }) } : run(args);
  });
  await screen.findByText("Saved reply excerpt");
  expect(screen.getByText("notes.md")).toBeTruthy();
  expect(document.querySelectorAll(".sl-msg")).toHaveLength(3);
  expect(screen.queryByTitle("Generation time, including tool work")).toBeNull();
  expect(screen.queryByText(/Tokens unavailable/)).toBeNull();
});
