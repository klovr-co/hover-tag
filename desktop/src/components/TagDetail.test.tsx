// Tag detail: the feed shows only Tag's own records, and model changes wait for a click.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AIModels, Permissions } from "../lib/ai";
import { type Bridge, demoBridge } from "../lib/bridge";
import { parseList, type TagRow } from "../lib/protocol";
import type { Tags } from "../lib/tags";
import { activityText, TagDetail } from "./TagDetail";

afterEach(cleanup);

async function open(tab?: string, canDescribe = false, configure?: (api: Bridge, rows: TagRow[]) => void, canRefreshModels = false) {
  const api = demoBridge();
  const rows: TagRow[] = parseList((await api.tag(["list", "--json"])).stdout);
  configure?.(api, rows);
  const calls: string[][] = [];
  const run = api.tag;
  api.tag = (args) => { calls.push(args); return run(args); };
  const remove = vi.fn(async () => true);
  const describe = vi.fn(async () => "");
  const tags = { rows, busy: new Set<string>(), toggle: vi.fn(), workspace: vi.fn(), rename: vi.fn(async () => ""), describe, refresh: vi.fn(), remove } as unknown as Tags;
  const say = vi.fn();
  const openAI = vi.fn();
  render(<TagDetail api={api} tags={tags} initial={rows[0].id} problems={{}} back={vi.fn()} add={vi.fn()} showSettings={vi.fn()}
    finishSetup={vi.fn()} openAI={openAI} say={say} canDescribe={canDescribe} canRefreshModels={canRefreshModels} />);
  if (tab) fireEvent.click(screen.getByRole("tab", { name: tab }));
  return { calls, say, rows, remove, describe, openAI };
}

describe("Tag detail", () => {
  it("shows the Tag's recorded replies, and nothing it didn't do", async () => {
    await open();
    expect((await screen.findAllByText("#launch")).length).toBeGreaterThan(0);
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

  it("shows why a change failed under the field being edited, and keeps it open to retry", async () => {
    const api = demoBridge();
    const rows: TagRow[] = parseList((await api.tag(["list", "--json"])).stdout);
    const failure = "Slack could not change the description (The request to Slack timed out); retry `tag maya describe \"Launch help\"`";
    const tags = { rows, busy: new Set<string>(), error: "", toggle: vi.fn(), workspace: vi.fn(),
      rename: vi.fn(async () => "Rename failed. Slack did not save the new name; retry `tag maya rename \"Maxine's Tag\"`"),
      describe: vi.fn(async () => failure), refresh: vi.fn(), remove: vi.fn() } as unknown as Tags;
    render(<TagDetail api={api} tags={tags} initial={rows[0].id} problems={{}} back={vi.fn()} add={vi.fn()} showSettings={vi.fn()}
      finishSetup={vi.fn()} openAI={vi.fn()} say={vi.fn()} canDescribe />);
    fireEvent.click(screen.getByRole("tab", { name: "Details" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Launch help" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const shown = await screen.findByText(/The request to Slack timed out/);
    expect(shown.closest(".desc-edit")).toBeTruthy();
    expect(screen.getByLabelText("Description")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    fireEvent.click(screen.getAllByRole("button", { name: "Save" })[0]);
    expect((await screen.findByText(/Slack did not save the new name/)).closest(".edit-err")).toBeTruthy();
  });

  it("shows the description but no Edit when the installed Tag can't change it", async () => {
    const { rows } = await open("Details");
    expect(screen.getByText(rows[0].description!)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  });

  it("labels runs a routine started, and the label opens the routine; a failure's reference finds its log lines", async () => {
    const api = demoBridge();
    const rows: TagRow[] = parseList((await api.tag(["list", "--json"])).stdout);
    const tags = { rows, busy: new Set<string>(), toggle: vi.fn(), workspace: vi.fn(), rename: vi.fn(async () => ""), describe: vi.fn(), refresh: vi.fn(), remove: vi.fn() } as unknown as Tags;
    render(<TagDetail api={api} tags={tags} initial={rows[0].id} problems={{}} back={vi.fn()} add={vi.fn()} showSettings={vi.fn()}
      finishSetup={vi.fn()} openAI={vi.fn()} say={vi.fn()} canRoutines />);
    // A NO_REPLY run posts nothing, so like any reply without text it isn't in the feed.
    const labels = await screen.findAllByRole("button", { name: /Routine: Weekly PR digest/ });
    expect(labels.length).toBeGreaterThan(0);
    fireEvent.click(labels[0]);
    expect(screen.getByRole("tab", { name: "Routines" }).getAttribute("aria-selected")).toBe("true");
    const history = await screen.findByRole("list", { name: "Weekly PR digest runs" });
    fireEvent.click(within(history).getAllByRole("button", { expanded: false }).at(-1)!);
    fireEvent.click(screen.getByRole("button", { name: "Find 7C2E91AB in logs" }));
    expect(screen.getByRole("tab", { name: "Logs" }).getAttribute("aria-selected")).toBe("true");
    expect(await screen.findByText(/1 line mentions 7C2E91AB/)).toBeTruthy();
    expect(document.querySelector(".logbox mark")?.textContent).toContain("Routine r-3f9a1c2e7b failed [7C2E91AB]");
    fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(document.querySelector(".logbox mark")).toBeNull();
  });

  it("shows routine labels as plain text where routines can't be opened", async () => {
    await open();
    expect((await screen.findAllByText(/Routine: Weekly PR digest/))[0].closest("button")).toBeNull();
  });

  it("shows the Tag's service log in a Logs tab", async () => {
    const { calls } = await open("Logs");
    await vi.waitFor(() => expect(screen.getByLabelText("Tag log").textContent).toContain("── slack ──"));
    expect(calls.some((args) => args[1] === "logs")).toBe(true);
  });

  it("says a running Tag lost Slack and points to its logs", async () => {
    await open(undefined, false, (_api, rows) => Object.assign(rows[0], { state: "needs_attention", services: { slack: false, mfs: true } }));
    expect((await screen.findAllByText("Not connected to Slack")).length).toBeGreaterThan(0);
    expect(screen.getByText(/stopped hearing from Slack/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "View logs" }));
    expect(screen.getByRole("tab", { name: "Logs" }).getAttribute("aria-selected")).toBe("true");
  });

  /** Opens Research Tag, the demo Tag whose model runs on Claude. */
  const claudeTag = (_api: Bridge, rows: TagRow[]) => rows.sort((a) => (a.slack_name === "Research Tag" ? -1 : 1));

  it("shows only the permissions of the agent the Tag's model runs on, and saves only what changed", async () => {
    const { calls, say } = await open("Details");
    const codex = await screen.findByRole("region", { name: "Codex permissions" }, { timeout: 5000 });
    // Every Slack request uses the Tag's model, here Codex's, so Claude's settings would never apply.
    expect(screen.queryByRole("region", { name: "Claude permissions" })).toBeNull();
    expect(within(within(codex).getByRole("radiogroup", { name: "Sandbox" })).getByRole("radio", { name: "Workspace write" }).getAttribute("aria-checked")).toBe("true");
    fireEvent.click(within(codex).getByRole("radio", { name: "Full access" }));
    expect(within(codex).getByText("No sandbox: changes anything on this computer as you.")).toBeTruthy();
    expect(screen.getByText("Restart Maya's Tag to use the new permissions.")).toBeTruthy();
    expect(calls.some((args) => args.includes("permissions"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Save and restart" }));
    await vi.waitFor(() => expect(calls).toContainEqual([expect.any(String), "settings", "ai", "permissions",
      "codex-sandbox=danger-full-access", "--restart", "--json"]));
    await vi.waitFor(() => expect(say).toHaveBeenCalledWith("Maya's Tag restarted with the new permissions"));
    expect(within(codex).getByRole("radio", { name: "Full access" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.queryByText(/Restart Maya's Tag/)).toBeNull();
  });

  it("shows Claude's mode for a Tag on a Claude model, and warns about Bypass permissions", async () => {
    const { calls } = await open("Details", false, claudeTag);
    const claude = await screen.findByRole("region", { name: "Claude permissions" }, { timeout: 5000 });
    expect(screen.queryByRole("region", { name: "Codex permissions" })).toBeNull();
    const mode = within(claude).getByRole<HTMLSelectElement>("combobox", { name: "Mode" });
    expect(mode.value).toBe("auto");
    expect(within(mode).getAllByRole("option").map((o) => o.textContent)).toEqual(["Default", "Accept edits", "Auto", "Don't ask", "Bypass permissions"]);
    fireEvent.change(mode, { target: { value: "bypassPermissions" } });
    expect(within(claude).getByText("Skips approvals. Only deny rules and hooks can stop it.")).toBeTruthy();
    fireEvent.change(mode, { target: { value: "acceptEdits" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(calls).toContainEqual([expect.any(String), "settings", "ai", "permissions",
      "claude-mode=acceptEdits", "--json"]));
  });

  it("hides who answers Codex when it never asks, and Cancel puts every setting back", async () => {
    await open("Details");
    const codex = await screen.findByRole("region", { name: "Codex permissions" }, { timeout: 5000 });
    expect(within(codex).getByRole("radiogroup", { name: "Approvals reviewer" })).toBeTruthy();
    fireEvent.click(within(codex).getByRole("radio", { name: "Never" }));
    expect(within(codex).queryByRole("radiogroup", { name: "Approvals reviewer" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(within(codex).getByRole("radio", { name: "Default" }).getAttribute("aria-checked")).toBe("true");
    expect(within(codex).getByRole("radiogroup", { name: "Approvals reviewer" })).toBeTruthy();
  });

  it("pins Codex's approvals over your own Codex config, even to its usual value", async () => {
    const { calls } = await open("Details");
    const codex = await screen.findByRole("region", { name: "Codex permissions" }, { timeout: 5000 });
    fireEvent.click(within(codex).getByRole("radio", { name: "On request" }));
    fireEvent.click(screen.getByRole("button", { name: "Save and restart" }));
    await vi.waitFor(() => expect(calls).toContainEqual([expect.any(String), "settings", "ai", "permissions",
      "codex-approvals=on-request", "--restart", "--json"]));
  });

  it("shows a saved value Tag won't run with, so a valid one can be picked", async () => {
    await open("Details", false, (api, rows) => {
      claudeTag(api, rows);
      const run = api.tag;
      api.tag = async (args) => {
        const result = await run(args);
        if (!(args[1] === "settings" && args[2] === "ai" && args[3] === "--json")) return result;
        const report = JSON.parse(result.stdout);
        report.permissions.claude.mode = "plan";
        report.permissions.errors = { "claude-mode": "OPENTAG_CLAUDE_PERMISSION_MODE must be one of default, …" };
        return { ...result, stdout: JSON.stringify(report) };
      };
    });
    const claude = await screen.findByRole("region", { name: "Claude permissions" }, { timeout: 5000 });
    expect(within(claude).getByText(/“plan” isn't a valid choice, so the Tag won't run/)).toBeTruthy();
    const mode = within(claude).getByRole<HTMLSelectElement>("combobox", { name: "Mode" });
    expect(mode.value).toBe("plan");
    fireEvent.change(mode, { target: { value: "auto" } });
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
  });

  it("keeps no Codex rules for a Tag working in a folder of yours", async () => {
    await open("Details", false, (api) => {
      const run = api.tag;
      api.tag = async (args) => {
        const result = await run(args);
        if (!(args[1] === "settings" && args[2] === "ai" && args[3] === "--json")) return result;
        const report = JSON.parse(result.stdout);
        report.permissions.own_folder = false;
        return { ...result, stdout: JSON.stringify(report) };
      };
    });
    const codex = await screen.findByRole("region", { name: "Codex permissions" }, { timeout: 5000 });
    fireEvent.click(within(codex).getByRole("button", { name: /^Codex rules:/ }));
    expect(within(codex).getByText(/works in a folder of yours/)).toBeTruthy();
    expect(within(codex).getByText(/won't run Codex until it has its own folder/)).toBeTruthy();
    expect(within(codex).queryByRole("textbox", { name: "Command start" })).toBeNull();
  });

  it("adds and removes Codex rules for this Tag", async () => {
    const { calls, say, openAI } = await open("Details");
    const codex = await screen.findByRole("region", { name: "Codex permissions" }, { timeout: 5000 });
    // Folded to one line with its counts until opened.
    expect(within(codex).queryByText("git push")).toBeNull();
    fireEvent.click(within(codex).getByRole("button", { name: "Codex rules: 2 for this Tag · 1 for all Tags" }));
    expect(within(codex).getByText("git push")).toBeTruthy();
    // The example isn't a rule it already has.
    expect(within(codex).getByRole("textbox", { name: "Command start" }).getAttribute("placeholder")).toBe("npm publish");
    // Rules for all Tags are changed in Settings, one click away.
    fireEvent.click(within(codex).getByRole("button", { name: /^Also follows/ }));
    fireEvent.click(within(codex).getByRole("button", { name: "Change them in Settings → AI connections" }));
    expect(openAI).toHaveBeenCalled();
    fireEvent.change(within(codex).getByRole("textbox", { name: "Command start" }), { target: { value: "curl" } });
    fireEvent.click(within(codex).getByRole("button", { name: "Add" }));
    await vi.waitFor(() => expect(calls).toContainEqual([expect.any(String), "settings", "ai", "permissions",
      "block", "codex", "curl", "--json"]));
    await vi.waitFor(() => expect(within(codex).getByText("curl")).toBeTruthy());
    expect(say).toHaveBeenCalledWith("Blocked curl for Maya's Tag. It applies from the next request.");
    expect((within(codex).getByRole("textbox", { name: "Command start" }) as HTMLInputElement).value).toBe("");
    fireEvent.click(within(codex).getByRole("button", { name: "Remove git push" }));
    await vi.waitFor(() => expect(within(codex).queryByText("git push")).toBeNull());
    expect(screen.queryByText(/Restart Maya's Tag/)).toBeNull();
  });

  /** Changes the Tag's AI report before the screen reads it. */
  const withReport = (change: (permissions: Permissions) => void) => (api: Bridge) => {
    const run = api.tag;
    api.tag = async (args) => {
      const result = await run(args);
      if (!(args[1] === "settings" && args[2] === "ai" && args[3] === "--json")) return result;
      const report = JSON.parse(result.stdout);
      change(report.permissions);
      return { ...result, stdout: JSON.stringify(report) };
    };
  };
  /** Answers rule changes with `answer` instead of the demo. */
  const ruleChanges = (answer: () => Promise<{ code: number; stdout: string; stderr: string }>) => (api: Bridge) => {
    const run = api.tag;
    api.tag = (args) => (args[3] === "permissions" && ["allow", "block", "remove"].includes(args[4]) ? answer() : run(args));
  };

  it("shows unreadable rules for this Tag as unreadable, not as none, and can't add to them", async () => {
    await open("Details", false, withReport((p) => { p.codex.rules!.tag = null; }));
    const codex = await screen.findByRole("region", { name: "Codex permissions" }, { timeout: 5000 });
    fireEvent.click(within(codex).getByRole("button", { name: "Codex rules: unreadable for this Tag · 1 for all Tags" }));
    expect(within(codex).getByText(/Couldn't read these Codex rules/)).toBeTruthy();
    expect(within(codex).queryByText("None yet.")).toBeNull();
    expect((within(codex).getByRole("textbox", { name: "Command start" }) as HTMLInputElement).disabled).toBe(true);
    expect((within(codex).getByRole("radio", { name: "Allow" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows an unreadable inherited scope as unreadable", async () => {
    await open("Details", false, withReport((p) => { p.codex.rules!.folder = null; }));
    const codex = await screen.findByRole("region", { name: "Codex permissions" }, { timeout: 5000 });
    fireEvent.click(within(codex).getByRole("button", { name: /^Codex rules: 2 for this Tag · unreadable in its folder/ }));
    fireEvent.click(within(codex).getByRole("button", { name: /^Also follows unreadable in its folder/ }));
    expect(within(codex).getByText("Couldn't read them.")).toBeTruthy();
  });

  it("lets a rule be changed again after the change couldn't run", async () => {
    await open("Details", false, ruleChanges(() => Promise.reject(new Error("Tag isn't installed"))));
    const codex = await screen.findByRole("region", { name: "Codex permissions" }, { timeout: 5000 });
    fireEvent.click(within(codex).getByRole("button", { name: /^Codex rules:/ }));
    const remove = within(codex).getByRole<HTMLButtonElement>("button", { name: "Remove git push" });
    fireEvent.click(remove);
    expect(await screen.findByText(/Tag isn't installed/)).toBeTruthy();
    expect(remove.disabled).toBe(false);
    expect(remove.closest("li")!.className).not.toContain("going");
    expect((within(codex).getByRole("textbox", { name: "Command start" }) as HTMLInputElement).disabled).toBe(false);
    expect(within(codex).getByText("git push")).toBeTruthy();
  });

  it("lets the permissions be saved again after saving couldn't run", async () => {
    await open("Details", false, (api) => {
      const run = api.tag;
      api.tag = (args) => (args[3] === "permissions" && args[4]?.includes("=") ? Promise.reject(new Error("Tag isn't installed")) : run(args));
    });
    const codex = await screen.findByRole("region", { name: "Codex permissions" }, { timeout: 5000 });
    fireEvent.click(within(codex).getByRole("radio", { name: "Full access" }));
    fireEvent.click(screen.getByRole("button", { name: "Save and restart" }));
    expect(await screen.findByText(/Tag isn't installed/)).toBeTruthy();
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Save and restart" }).disabled).toBe(false);
    expect(within(codex).getByRole<HTMLButtonElement>("radio", { name: "Read only" }).disabled).toBe(false);
  });

  it("keeps Save waiting while a rule changes, and dims only that rule", async () => {
    let finish = () => {};
    await open("Details", false, (api) => {
      const run = api.tag;
      api.tag = (args) => args[3] === "permissions" && args[4] === "remove"
        ? new Promise((resolve) => { finish = () => resolve(run(args)); })
        : run(args);
    });
    const codex = await screen.findByRole("region", { name: "Codex permissions" }, { timeout: 5000 });
    fireEvent.click(within(codex).getByRole("radio", { name: "Full access" }));
    const save = screen.getByRole<HTMLButtonElement>("button", { name: "Save and restart" });
    expect(save.disabled).toBe(false);
    fireEvent.click(within(codex).getByRole("button", { name: /^Codex rules:/ }));
    fireEvent.click(within(codex).getByRole("button", { name: "Remove git push" }));
    await vi.waitFor(() => expect(save.disabled).toBe(true));
    expect(within(codex).getByText("git push").closest("li")!.className).toContain("going");
    expect(within(codex).getByText("npm run build").closest("li")!.className).not.toContain("going");
    await act(async () => finish());
    await vi.waitFor(() => expect(within(codex).queryByText("git push")).toBeNull());
    expect(save.disabled).toBe(false);
  });

  it("moves focus to the next rule after a removal, then to the field, and keeps it there after a failed add", async () => {
    await open("Details");
    const codex = await screen.findByRole("region", { name: "Codex permissions" }, { timeout: 5000 });
    fireEvent.click(within(codex).getByRole("button", { name: /^Codex rules:/ }));
    const first = within(codex).getByRole("button", { name: "Remove npm run build" });
    first.focus();
    fireEvent.click(first);
    await vi.waitFor(() => expect(document.activeElement).toBe(within(codex).getByRole("button", { name: "Remove git push" })));
    fireEvent.click(within(codex).getByRole("button", { name: "Remove git push" }));
    const field = within(codex).getByRole<HTMLInputElement>("textbox", { name: "Command start" });
    await vi.waitFor(() => expect(document.activeElement).toBe(field));
    expect(within(codex).getByText("None yet.")).toBeTruthy();
    fireEvent.change(field, { target: { value: "-x" } });
    fireEvent.click(within(codex).getByRole("button", { name: "Add" }));
    await vi.waitFor(() => expect(screen.getByText(/Write the start of a command/)).toBeTruthy());
    await vi.waitFor(() => expect(document.activeElement).toBe(field));
    expect(field.value).toBe("-x");
  });

  it("shows a reviewer Codex won't start with even when it never asks", async () => {
    await open("Details", false, withReport((p) => {
      p.codex.approvals = "never";
      p.codex.reviewer = "robot";
      p.errors = { "codex-reviewer": "OPENTAG_CODEX_APPROVALS_REVIEWER must be one of auto_review, user" };
    }));
    const codex = await screen.findByRole("region", { name: "Codex permissions" }, { timeout: 5000 });
    expect(within(codex).getByRole("radiogroup", { name: "Approvals reviewer" })).toBeTruthy();
    expect(within(codex).getByText(/“robot” isn't a valid choice, so the Tag won't run/)).toBeTruthy();
    expect(within(codex).getByText("OPENTAG_CODEX_APPROVALS_REVIEWER must be one of auto_review, user")).toBeTruthy();
    // Once a valid one is picked, it hides again, since Codex never asks.
    fireEvent.click(within(codex).getByRole("radio", { name: "User" }));
    expect(within(codex).queryByRole("radiogroup", { name: "Approvals reviewer" })).toBeNull();
  });

  it("adds and removes Claude rules for this Tag, and only shows the ones for all Tags", async () => {
    const { calls } = await open("Details", false, claudeTag);
    const claude = await screen.findByRole("region", { name: "Claude permissions" }, { timeout: 5000 });
    fireEvent.click(within(claude).getByRole("button", { name: /^Claude rules:/ }));
    // Rules for all Tags and your own are shown, and can't be removed here.
    fireEvent.click(within(claude).getByRole("button", { name: "Also follows 1 for all Tags and 1 of your own" }));
    expect(within(claude).getByText("Bash(rm -rf:*)")).toBeTruthy();
    expect(within(claude).getByText("WebFetch")).toBeTruthy();
    expect(within(claude).queryByRole("button", { name: "Remove Bash(rm -rf:*)" })).toBeNull();
    fireEvent.click(within(claude).getByRole("radio", { name: "Allow" }));
    fireEvent.change(within(claude).getByRole("textbox", { name: "Claude rule" }), { target: { value: "not a rule" } });
    fireEvent.click(within(claude).getByRole("button", { name: "Add" }));
    await vi.waitFor(() => expect(screen.getByText(/Write a Claude rule/)).toBeTruthy());
    fireEvent.click(within(claude).getByRole("button", { name: "Remove Bash(git status:*)" }));
    await vi.waitFor(() => expect(calls).toContainEqual([expect.any(String), "settings", "ai", "permissions",
      "remove", "claude", "Bash(git status:*)", "--json"]));
    await vi.waitFor(() => expect(within(claude).queryByText("Bash(git status:*)")).toBeNull());
    expect(within(claude).getByText("/Users/maya/Projects/site")).toBeTruthy();
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

it("groups replies in one Slack thread into one entry led by the latest reply", async () => {
  await open(undefined, false, (api) => {
    const run = api.tag;
    api.tag = async (args) => args[1] === "logs" ? { code: 0, stderr: "", stdout: JSON.stringify({ services: {}, activity: [
      { ...entry("Started the issue review", "2026-10-05T01:00:00Z"), thread: "a1" },
      { ...entry("Separate question", "2026-10-05T01:30:00Z"), thread: "b2" },
      { ...entry("Closed 12 of 67 issues", "2026-10-05T02:00:00Z"), thread: "a1" },
      entry("Older Tag without threads", "2026-10-05T00:30:00Z"),
    ] }) } : run(args);
  });
  await screen.findByText("Closed 12 of 67 issues");
  const messages = () => [...document.querySelectorAll(".sl-msg p")].map((el) => el.textContent);
  expect(messages()).toEqual(["Older Tag without threads", "Separate question", "Closed 12 of 67 issues"]);
  const toggle = screen.getByRole("button", { name: "Show 1 earlier reply in this thread" });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(toggle);
  expect(messages()).toEqual(["Older Tag without threads", "Separate question", "Closed 12 of 67 issues", "Started the issue review"]);
  expect(screen.getByRole("button", { name: "Hide 1 earlier reply in this thread" }).getAttribute("aria-expanded")).toBe("true");
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
}, 20_000);

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
}, 20_000);


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

it("counts a Tag's channels instead of listing them in the header", async () => {
  await open(undefined, false, (_api, rows) => {
    rows[0].channels = [{ id: "C1", name: "launch" }, { id: "C2", name: "general" }, { id: "C3", name: "ops" }];
  });
  expect(document.querySelector(".sl-mhead .sub")?.textContent).toMatch(/ · in 3 channels$/);
  cleanup();
  await open(undefined, false, (_api, rows) => { rows[0].channels = [{ id: "C1", name: "launch" }]; });
  expect(document.querySelector(".sl-mhead .sub")?.textContent).toMatch(/ · in #launch$/);
});

describe("Refresh list in the model menu", () => {
  type Answer = { code: number; stdout: string; stderr: string };
  /** `models --refresh` waits for `answer`; `change` edits the list it then returns. */
  function refreshing(change: (models: AIModels) => AIModels | Answer) {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const configure = (api: Bridge) => {
      const run = api.tag;
      api.tag = async (args) => {
        if (!args.includes("--refresh")) return run(args);
        await gate;
        const listed = await run(args.filter((a) => a !== "--refresh"));
        const next = change(JSON.parse(listed.stdout) as AIModels);
        return "code" in next ? next : { ...listed, stdout: JSON.stringify(next) };
      };
    };
    return { configure, release };
  }

  async function openMenu() {
    fireEvent.click(await screen.findByRole("button", { name: "Default model" }, { timeout: 5000 }));
    return screen.getByRole("listbox", { name: "Default model" });
  }

  it("reads the accounts' lists again and shows the new model in place", async () => {
    const refresh = refreshing((models) => ({ ...models, refresh_failed: [], groups: models.groups.map((g) => g.backend !== "codex" ? g
      : { ...g, cached: false, models: [...g.models, { value: "codex:gpt-6", model: "gpt-6", label: "GPT-6", default: false, efforts: [], default_effort: null }] }) }));
    const { calls } = await open("Details", false, refresh.configure, true);
    const menu = await openMenu();
    expect(within(menu).queryByRole("option", { name: /GPT-6/ })).toBeNull();
    fireEvent.click(within(menu).getByRole("button", { name: "Refresh list" }));
    const busy = within(menu).getByRole("button", { name: "Refreshing…" });
    expect((busy as HTMLButtonElement).disabled).toBe(true);
    expect(calls).toContainEqual([expect.any(String), "settings", "ai", "models", "--refresh", "--json"]);
    // The list stays usable while it refreshes.
    expect(within(menu).getByRole("option", { name: /GPT-5\.5(?! mini)/ })).toBeTruthy();
    await act(async () => refresh.release());
    expect(await within(menu).findByRole("option", { name: /GPT-6/ }, { timeout: 5000 })).toBeTruthy();
    expect(within(menu).getByRole("button", { name: "Refresh list" })).toBeTruthy();
    expect(within(menu).queryByRole("status")).toBeNull();
  });

  it("keeps the list it shows when the refresh fails", async () => {
    const refresh = refreshing(() => ({ code: 1, stdout: "", stderr: "Codex didn't start" }));
    await open("Details", false, refresh.configure, true);
    const menu = await openMenu();
    const before = within(menu).getAllByRole("option").map((o) => o.textContent);
    fireEvent.click(within(menu).getByRole("button", { name: "Refresh list" }));
    await act(async () => refresh.release());
    expect((await within(menu).findByRole("status", {}, { timeout: 5000 })).textContent).toBe("Couldn't refresh the list. Showing the saved one.");
    expect(within(menu).getAllByRole("option").map((o) => o.textContent)).toEqual(before);
    expect(within(menu).getByRole("button", { name: "Refresh list" })).toBeTruthy();
  });

  it("names the account whose list couldn't be read, and keeps its saved list", async () => {
    const refresh = refreshing((models) => ({ ...models, refresh_failed: ["codex"] }));
    await open("Details", false, refresh.configure, true);
    const menu = await openMenu();
    fireEvent.click(within(menu).getByRole("button", { name: "Refresh list" }));
    await act(async () => refresh.release());
    expect((await within(menu).findByRole("status", {}, { timeout: 5000 })).textContent).toBe("Couldn't refresh Codex. Showing the saved list.");
    expect(within(menu).getByRole("option", { name: /GPT-5\.5(?! mini)/ })).toBeTruthy();
  });

  it("isn't offered by a Tag that can't refresh its lists", async () => {
    await open("Details");
    const menu = await openMenu();
    expect(within(menu).getAllByRole("option").length).toBeGreaterThan(0);
    expect(within(menu).queryByRole("button", { name: /Refresh/ })).toBeNull();
  });
});
