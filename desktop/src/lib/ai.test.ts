// Reads the same AI examples tests/test_tag_ai.py checks real CLI output against.
import { describe, expect, it } from "vitest";
import status from "../../../protocol/examples/ai-status.json";
import models from "../../../protocol/examples/ai-models.json";
import saved from "../../../protocol/examples/ai-model.json";
import signIn from "../../../protocol/examples/ai-sign-in.jsonl?raw";
import setup from "../../../protocol/examples/setup.jsonl?raw";
import {
  choiceLabel, findModel, idleSignIn, parseModels, parseSignInLine, parseStatus, primaryAction,
  resultLine, selectedModel, signInArgs, signInReducer, statusLine,
} from "./ai";
import { initialSetup, setupReducer } from "./setup";

const [codex, claude] = parseStatus(JSON.stringify(status)).connections;

describe("AI status", () => {
  it("reads connections and words their state", () => {
    const report = parseStatus("warning\n" + JSON.stringify(status));
    expect(report.usable).toEqual(["codex"]);
    expect(statusLine(codex)).toEqual({ text: "Connected · ChatGPT sign-in", tone: "good" });
    expect(statusLine(claude)).toEqual({ text: "Sign-in expired", tone: "warn" });
    expect(choiceLabel(report.default_model)).toBe("Codex · GPT-5.5");
  });

  it("offers the most urgent action first", () => {
    expect(primaryAction(claude)).toBe("reconnect");
    expect(primaryAction(codex)).toBe("change_account");
    expect(primaryAction({ ...codex, actions: ["resume", "change_account"] })).toBe("resume");
    expect(primaryAction({ ...codex, actions: [] })).toBeNull();
    expect(statusLine({ ...codex, state: "something_new" }).text).toBe("Needs attention");
  });

  it("knows which account changes stop a running Tag", () => {
  });
});

describe("models", () => {
  it("groups models by agent and selects the saved default while it's offered", () => {
    const catalog = parseModels(JSON.stringify(models));
    expect(catalog.groups.map((g) => g.name)).toEqual(["Codex", "Claude"]);
    expect(selectedModel(catalog)).toBe("codex:gpt-5.5");
    expect(findModel(catalog.groups, "claude:claude-opus-5-5")?.group.backend).toBe("claude");
    const gone = { ...catalog, default: { ...catalog.default, value: "codex:gpt-5.4", available: false } };
    expect(selectedModel(gone)).toBe(catalog.suggested);
    expect(saved.restarted).toBe(true);
  });
});

describe("sign-in", () => {
  const lines = signIn.trim().split("\n");

  it("walks the example from browser to connected", () => {
    let state = signInReducer(idleSignIn, { type: "start", backend: "claude" });
    const steps: string[] = [];
    for (const line of lines) {
      state = signInReducer(state, { type: "line", line });
      if (state.step) steps.push(state.step);
    }
    expect(steps).toEqual(["browser", "waiting", "waiting", "verifying"]);
    expect(state.url).toMatch(/^https:\/\/claude\.ai\//);
    expect(state.result).toMatchObject({ status: "connected", restart_required: true });
    expect(resultLine(state.result!, "Claude")).toBe("Claude connected.");
  });

  it("reports a command that failed or exited without a result", () => {
    let state = signInReducer(idleSignIn, { type: "start", backend: "codex" });
    state = signInReducer(state, { type: "line", line: '{"schema_version": 1, "ok": false, "error": "Stop Maya first"}' });
    expect(state.result).toMatchObject({ status: "failed", error: "Stop Maya first", retry: true });
    state = signInReducer(signInReducer(idleSignIn, { type: "start", backend: "codex" }), { type: "exit", code: 1, stderr: "Error: boom\n" });
    expect(resultLine(state.result!, "Codex")).toBe("Sign-in didn't finish. boom");
    expect(parseSignInLine("Opening browser")).toBeNull();
  });

  it("builds the sign-in command for each method", () => {
    expect(signInArgs("codex", { method: "chatgpt", restart: true }))
      .toEqual(["settings", "ai", "sign-in", "codex", "--method", "chatgpt", "--restart"]);
    expect(signInArgs("codex", { resume: true, restart: true })).toEqual(["settings", "ai", "resume", "--restart"]);
    expect(signInArgs("claude")).toEqual(["settings", "ai", "sign-in", "claude"]);
  });
});

describe("setup's AI step", () => {
  it("offers models from shared connections without a sign-in step", () => {
    const lines = setup.trim().split("\n");
    const asked = lines.findIndex((line) => line.includes('"default_model"'));
    let state = initialSetup;
    for (const line of lines.slice(0, asked + 1)) state = setupReducer(state, { type: "line", line });
    expect(state.question?.id).toBe("default_model");
    const question = JSON.parse(lines[asked]);
    expect(question.option_ids).toEqual(["codex", "codex:gpt-5.5", "claude", "claude:claude-opus-5-5"]);
    expect(question.connections.every((connection: { shared: boolean }) => connection.shared)).toBe(true);
    expect(lines.some((line) => JSON.parse(line).type === "sign_in")).toBe(false);
  });
});
