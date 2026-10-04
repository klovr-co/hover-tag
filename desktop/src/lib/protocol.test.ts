// The app's half of the contract tests: it must read every shared example,
// which tests/test_app_protocol.py checks real CLI output against.
import { describe, expect, it } from "vitest";
import list from "../../../protocol/examples/list.json";
import logs from "../../../protocol/examples/logs.json";
import aiStatus from "../../../protocol/examples/ai-status.json";
import version from "../../../protocol/examples/version.json";
import progress from "../../../protocol/examples/install-progress.txt?raw";
import setup from "../../../protocol/examples/setup.jsonl?raw";
import { fraction, initialInstall, installReducer } from "./install";
import {
  compatibility, groups, INSTALL_STEPS, parseList, parseProgressLine, parseSetupLine, status, title,
  type VersionInfo,
} from "./protocol";
import { explainExit, initialSetup, setupReducer, trackStep } from "./setup";
import { droppedTags, failureLine } from "./tags";
import { workingFolder } from "../components/Home";
import { modelText, quietLine, rowLine, type ActivityItem } from "./home";
import { parseStatus } from "./ai";

describe("tag list", () => {
  it("reads the example, even after warnings", () => {
    const rows = parseList("warning: something\n" + JSON.stringify(list));
    expect(rows.map(title)).toEqual(["Maya's Tag", "New Tag"]);
    expect(rows.map(status)).toEqual(["online", "setup"]);
  });

  it("groups by workspace in list order", () => {
    const rows = parseList(JSON.stringify(list));
    expect(groups(rows).map((g) => g.label)).toEqual(["Klovr", "Acme Inc"]);
    expect(groups([{ id: "x", valid: true }])[0].label).toBe("Not connected yet");
  });

  it("uses the Slack workspace icon when Tag has saved one", () => {
    const rows = parseList(JSON.stringify(list));
    expect(groups(rows).map((g) => g.icon)).toEqual(["/Users/maya/Tag/t0klovr1-a0maya01/.tag/state/workspace-icon.png", null]);
    expect(groups([{ id: "a", valid: true, slack_workspace: "T1" }, { id: "b", valid: true, slack_workspace: "T1", workspace_icon: "/b.png" }])[0].icon).toBe("/b.png");
  });

  it("reads each Tag's description, model and thinking level for Home's rows", () => {
    const [maya, unfinished] = parseList(JSON.stringify(list));
    expect(modelText(maya)).toEqual({ text: "GPT-5.5 · med", model: "GPT-5.5", effort: "medium" });
    expect(rowLine(maya, null, "")).toEqual({ kind: "description", text: "I'm Maya's personal assistant. I help with launch work." });
    expect(unfinished.description).toBeNull();
    expect(unfinished.default_effort).toBeNull();
  });

  it("treats unknown states as needing attention", () => {
    expect(status({ id: "x", valid: true, state: "something_new" })).toBe("attention");
  });

  it("opens the working folder that holds a Tag's .tag data", () => {
    expect(workingFolder({ id: "a", valid: true, home: "/Users/maya/Tag/a/.tag" })).toBe("/Users/maya/Tag/a");
    expect(workingFolder({ id: "a", valid: true, home: "/tmp/home/instances/a" })).toBe("/tmp/home/instances/a/workspace");
  });
});

describe("compatibility", () => {
  it("accepts the example and names missing capabilities", () => {
    const info = version as VersionInfo;
    expect(compatibility(info, ["list", "setup-jsonl"]).ok).toBe(true);
    expect(compatibility(info, ["logs-json"])).toMatchObject({ ok: false, reason: "tag-too-old", missing: ["logs-json"] });
    expect(compatibility({ ...info, app_protocol: 99 }, []).reason).toBe("app-too-old");
  });
});

describe("setup", () => {
  const lines = setup.trim().split("\n");

  it("parses every example event and ignores ordinary output", () => {
    expect(lines.map(parseSetupLine).every(Boolean)).toBe(true);
    expect(parseSetupLine("Opening Slack…")).toBeNull();
    expect(parseSetupLine("{not json")).toBeNull();
  });

  it("walks the example conversation to a finished Tag, in onboarding order", () => {
    let state = initialSetup;
    const asked: string[] = [];
    const steps: number[] = [];
    for (const line of lines) {
      state = setupReducer(state, { type: "line", line });
      if (state.question && asked.at(-1) !== state.question.id) { asked.push(state.question.id); steps.push(trackStep(state)); }
    }
    expect(asked).toEqual(["profile", "default_model", "workspace", "approve_setup", "channels"]);
    expect(steps).toEqual([0, 1, 2, 3, 4]);
    expect(asked).not.toContain("person");
    expect(state).toMatchObject({ outcome: "complete", tag: "t0bnd7v5j2w-a0maya01", question: null,
      ready: { team: "T0BND7V5J2W", app_id: "A0MAYA01" }, profile: { name: "Maya's Tag" } });
  });

  it("shows creating the Slack app as its own steps, apart from agent sign-in", () => {
    let state = initialSetup;
    for (const line of lines.filter((l) => l.includes('"progress"'))) state = setupReducer(state, { type: "line", line });
    expect(state.creating?.map((s) => s.step)).toEqual(["create", "picture", "install", "connect"]);
    expect(state.signIn.backend).toBeNull();
  });

  it("stays on the code step when Slack refuses a code", () => {
    const login = JSON.stringify({ type: "question", id: "slack_login", kind: "slack_login", prompt: "Sign in to Slack",
      sign_in_line: "/slackauthticket ABC123", can_go_back: false });
    let state = setupReducer(initialSetup, { type: "line", line: login });
    state = setupReducer(state, { type: "signIn", step: 2 });
    state = setupReducer(state, { type: "answered" });
    expect(state.question?.kind).toBe("slack_login");
    state = setupReducer(state, { type: "line", line: login });
    expect(state.signInStep).toBe(2);
    expect(state.error).toMatch(/didn't accept/);
  });

  it("explains a setup that ended without a result", () => {
    let state = setupReducer(initialSetup, { type: "exit", code: 2, stderr: "usage\ntag_cli.py: error: --json supports list" });
    expect(state.outcome).toBe("failed");
    expect(state.error).toMatch(/Update Tag/);
    state = setupReducer(initialSetup, { type: "line", line: lines.at(-1)! });
    expect(setupReducer(state, { type: "exit", code: 0, stderr: "" }).outcome).toBe("complete");
    expect(explainExit("")).toMatch(/terminal/);
  });
});

describe("install progress", () => {
  const lines = progress.trim().split("\n");

  it("parses the example and covers every step in order", () => {
    const steps = lines.map((l) => parseProgressLine(l)!.step);
    expect(steps.filter((s) => INSTALL_STEPS.some((x) => x.step === s))).toEqual(INSTALL_STEPS.map((s) => s.step));
  });

  it("advances, records the version and command, and finishes", () => {
    let state = initialInstall;
    for (const line of lines) state = installReducer(state, { type: "line", line });
    expect(state).toMatchObject({ version: "0.2.0", command: "/Users/maya/.local/bin/tag", current: 5 });
    state = installReducer(state, { type: "exit", code: 0 });
    expect(state.finished).toBe(true);
    expect(fraction(state, 0)).toBe(1);
  });

  it("never moves backwards, and skipped steps count as done", () => {
    let state = installReducer(initialInstall, { type: "line", line: lines[4] }); // components
    expect(state.states.slice(0, 4)).toEqual(["done", "done", "done", "running"]);
    state = installReducer(state, { type: "line", line: lines[1] }); // python, late
    expect(state.current).toBe(3);
  });

  it("reports the installer's own failure message", () => {
    let state = installReducer(initialInstall, { type: "line", line: "Installation failed: no network" });
    state = installReducer(state, { type: "exit", code: 1 });
    expect(state.failure).toBe("no network");
    expect(state.states[0]).toBe("failed");
    state = installReducer(initialInstall, { type: "line", line: '@tag-progress {"step":"failed","message":"disk full"}' });
    expect(state.failure).toBe("disk full");
  });

  it("creeps within a step but never past it", () => {
    expect(fraction(initialInstall, 1e6)).toBeLessThan(INSTALL_STEPS[0].weight);
    expect(fraction(initialInstall, 10)).toBeGreaterThan(0);
  });
});

describe("tags", () => {
  it("notices Tags that went offline unexpectedly", () => {
    const before = [{ id: "a", valid: true, state: "running" }, { id: "b", valid: true, state: "running" }];
    const after = [{ id: "a", valid: true, state: "stopped" }, { id: "b", valid: true, state: "stopped" }];
    expect(droppedTags(before, after, new Set(["b"])).map((r) => r.id)).toEqual(["a"]);
  });

  it("shows the last line Tag printed when something fails", () => {
    expect(failureLine({ code: 1, stdout: "", stderr: "Traceback\nError: Slack refused\n" }, "x")).toBe("Slack refused");
    expect(failureLine({ code: 1, stdout: "", stderr: "" }, "fallback")).toBe("fallback");
  });
});

describe("tag logs activity", () => {
  it("finds the latest reply for Home's quiet line", () => {
    const rows = parseList(JSON.stringify(list));
    const activity = { [rows[0].id]: logs.activity as ActivityItem[] };
    const latest = new Date(logs.activity[0].at);
    const line = quietLine([...rows, rows[0], rows[0]], {}, activity, latest, "Maya");
    expect(line).toMatchObject({ kind: "reply", name: "Maya's Tag", today: true, place: "#launch" });
  });
});

describe("tag settings ai", () => {
  it("reads the thinking level beside the default model", () => {
    const report = parseStatus(JSON.stringify(aiStatus));
    expect(report.default_effort).toBe("medium");
    expect(report.effort_levels).toContain("high");
    expect(typeof report.effort_chosen).toBe("boolean");
  });
});
