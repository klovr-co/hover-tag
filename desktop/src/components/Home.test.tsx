// Home's quiet line and Tag rows, from what Tag reports.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import list from "../../../protocol/examples/list.json";
import aiStatus from "../../../protocol/examples/ai-status.json";
import type { AIStatus } from "../lib/ai";
import { demoBridge } from "../lib/bridge";
import { aiProblem, quietLine, roster, type ActivityItem } from "../lib/home";
import type { TagRow } from "../lib/protocol";
import type { Tags } from "../lib/tags";
import { Quiet, TagRowView } from "./Home";

afterEach(cleanup);

const maya: TagRow = { ...list.tags[0], description: "I'm Maya's personal assistant. I help with launch work.",
  default_model: "codex:gpt-5.5", default_model_name: "GPT-5.5", default_effort: "medium" };
const research: TagRow = { ...maya, id: "research", slack_name: "Research Tag", state: "stopped",
  description: "Digs through docs and old threads to answer research questions.",
  default_model: "claude:claude-opus-5-5", default_model_name: "Opus 5.5", default_effort: "max" };
const ops: TagRow = { ...maya, id: "ops", slack_name: "Ops Tag", slack_workspace: "T0ACME01", workspace_name: "Acme Inc",
  description: "Keeps on-call notes and launch checklists up to date.", default_model_name: "GPT-5.5 mini" };
const rows = [maya, research, ops];
const noon = new Date(2026, 9, 4, 12, 30);
const at = (day: number, hours: number, minutes: number) => new Date(2026, 9, day, hours, minutes).toISOString();
const reply = (when: string, channel = "launch-ops"): ActivityItem => ({ at: when, kind: "replied", channel: "C1", channel_name: channel, dm: false });

function report(backend: "codex" | "claude", state: string): AIStatus {
  const base = structuredClone(aiStatus) as AIStatus;
  base.connections = base.connections.map((c) => (c.backend === backend ? { ...c, state } : { ...c, state: "connected" }));
  base.default_model = { ...base.default_model, backend, label: backend === "claude" ? "Opus 5.5" : "GPT-5.5" };
  return base;
}

function quiet(problems: Record<string, string | null>, activity: Record<string, ActivityItem[]>, now = noon) {
  const open = vi.fn();
  const fix = vi.fn();
  render(<Quiet line={quietLine(rows, problems, activity, now, "Maya")} rows={rows} open={open} fix={fix} />);
  return { open, fix };
}

describe("Home's header roster", () => {
  it("shows only online Tags, and nothing when none are online", () => {
    expect(roster(rows).shown.map((row) => row.id)).toEqual([maya.id, "ops"]);
    expect(roster(rows.map((row) => ({ ...row, state: "stopped" })))).toEqual({ shown: [], extra: 0 });
    const many = Array.from({ length: 6 }, (_, i) => ({ ...maya, id: `t${i}` }));
    expect(roster([...many, research])).toMatchObject({ extra: 2 });
  });
});

describe("Home's quiet line", () => {
  it("leads with an AI that can't answer, grouped by its cause, and Fix opens AI & models", () => {
    const { fix } = quiet({ maya: null, research: "Codex isn't signed in", [maya.id]: "Codex isn't signed in" },
      { ops: [reply(at(4, 10, 12))] });
    expect(screen.getByRole("status").textContent).toBe("Codex isn't signed in · Maya's Tag and Research Tag can't answerFix");
    fireEvent.click(screen.getByRole("button", { name: "Fix" }));
    expect(fix).toHaveBeenCalledWith(maya.id);
  });

  it("names every cause when Tags fail for different reasons", () => {
    quiet({ [maya.id]: "Codex isn't signed in", research: "Claude sign-in expired" }, {});
    expect(screen.getByRole("status").textContent).toContain("Maya's Tag and Research Tag can't answer · Codex isn't signed in, Claude sign-in expired");
  });

  it("otherwise shows the latest reply today, which opens that Tag", () => {
    const { open } = quiet({}, { [maya.id]: [reply(at(4, 9, 20), "launch")], ops: [reply(at(4, 10, 12))] });
    const line = screen.getByRole("button");
    expect(line.textContent).toBe("Ops Tag just replied in #launch-ops10:12");
    fireEvent.click(line);
    expect(open).toHaveBeenCalledWith(ops);
  });

  it("says last replied, with the day, when nothing happened today", () => {
    quiet({}, { ops: [reply(at(3, 10, 12))] });
    expect(screen.getByRole("button").textContent).toBe("Ops Tag last replied in #launch-opsYesterday 10:12");
  });

  it("greets when there's no activity, and says whether the Tags are listening", () => {
    quiet({}, { ops: [{ ...reply(at(4, 10, 12)), kind: "failed" }] }, new Date(2026, 9, 4, 19, 0));
    expect(screen.getByText(/Good evening, Maya\./).parentElement!.textContent).toBe("Good evening, Maya. Your Tags are listening in Slack.");
    cleanup();
    const asleep = rows.map((row) => ({ ...row, state: "stopped" }));
    render(<Quiet line={quietLine(asleep, {}, {}, new Date(2026, 9, 4, 8, 0), null)} rows={asleep} open={vi.fn()} fix={vi.fn()} />);
    expect(screen.getByText(/Good morning\./).parentElement!.textContent).toBe("Good morning. Your Tags are taking a break.");
  });

  it("reads the cause from the default model's connection", () => {
    expect(aiProblem(report("codex", "signed_out"))).toBe("Codex isn't signed in");
    expect(aiProblem(report("claude", "expired"))).toBe("Claude sign-in expired");
    expect(aiProblem(report("codex", "not_installed"))).toBe("Codex isn't installed");
    expect(aiProblem(report("codex", "unsupported"))).toBe("Codex needs an update");
    // A usage limit only makes tasks wait, so it isn't a problem here.
    expect(aiProblem(report("codex", "limited"))).toBeNull();
    expect(aiProblem(report("codex", "connected"))).toBeNull();
  });
});

function row(tag: TagRow, problem: string | null = null) {
  const tags = { busy: new Set<string>(), toggle: vi.fn() } as unknown as Tags;
  const open = vi.fn();
  render(<TagRowView row={tag} tags={tags} problem={problem} open={open} />);
  return { open, tags };
}

describe("Tag rows", () => {
  it("show the name, the model and a short thinking level, then the description", () => {
    const { open } = row(maya);
    const item = screen.getByRole("button", { name: "Open Maya's Tag, online, GPT-5.5, Medium thinking" });
    expect(item.textContent).toContain("Maya's TagGPT-5.5 · med");
    expect(item.textContent).toContain("I'm Maya's personal assistant. I help with launch work.");
    expect(screen.getByText("GPT-5.5 · med").className).toBe("mname");
    fireEvent.click(item);
    expect(open).toHaveBeenCalled();
  });

  it("replace the description with the AI problem, in amber", () => {
    row(research, "Claude sign-in expired");
    expect(screen.getByText("Can't answer · Claude sign-in expired").parentElement!.className).toBe("sub warnline");
    expect(screen.getByText("Opus 5.5 · max").className).toBe("mname bad");
    expect(screen.queryByText(research.description!)).toBeNull();
  });

  it("show the error in red when the Tag needs attention", () => {
    row({ ...maya, state: "needs_attention", error: "Slack sign-in expired" });
    expect(screen.getByText("Slack sign-in expired").parentElement!.className).toBe("sub bad");
    expect(screen.getByRole("button", { name: /stopped/ })).toBeTruthy();
  });

  it("keep status to the badge and the switch, which doesn't open the Tag", () => {
    const { open, tags } = row(maya);
    expect(screen.queryByText("Online")).toBeNull();
    fireEvent.click(screen.getByRole("switch", { name: "Stop Maya's Tag" }));
    expect(tags.toggle).toHaveBeenCalledWith(maya);
    expect(open).not.toHaveBeenCalled();
  });

  it("show a model without thinking levels on its own", () => {
    row({ ...maya, default_model_name: "Haiku 4.5", default_effort: null });
    expect(screen.getByText("Haiku 4.5")).toBeTruthy();
  });

  it("list unfinished Tags in Finish setting up, with Continue", async () => {
    const { Home } = await import("./Home");
    const setup = list.tags[1] as TagRow;
    const tags = { rows: [maya, research, ops, setup], loaded: true, busy: new Set<string>(), error: "" } as unknown as Tags;
    const finish = vi.fn();
    render(<Home api={demoBridge()} tags={tags} reports={{}} problems={{}} activity={{}} firstName="Maya"
      update={{ status: "current", update: null, phase: null, error: "" }} outdated={false} runUpdate={vi.fn()}
      add={vi.fn()} finishSetup={finish} open={vi.fn()} fixAI={vi.fn()} showSettings={vi.fn()} now={noon} />);
    expect(screen.getByText("Finish setting up")).toBeTruthy();
    expect(screen.getByText("Not in Slack yet")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Continue/ }));
    expect(finish).toHaveBeenCalledWith(setup);
    expect(screen.getByText("2 of 3 online · 1 to finish")).toBeTruthy();
  });
});
