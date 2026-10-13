// Quick add asks only for the name, description and picture, then creates the Tag in place.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { demoBridge, type Session } from "../lib/bridge";
import type { SetupQuestion } from "../lib/protocol";
import { quickAnswer } from "../lib/setup";
import { QuickAdd, quickStep } from "./QuickAdd";

vi.mock("@tauri-apps/api/core", () => ({ convertFileSrc: (path: string) => `asset://localhost${encodeURIComponent(path)}` }));
afterEach(() => cleanup());

const QUESTIONS: Record<string, object> = {
  profile: { id: "profile", kind: "profile_picture", prompt: "Meet your new Tag", name: "Maya's Tag", name_limit: 35, description: "",
    description_limit: 140, preview: null, picture: "waterdrop", error: null, editing: false, can_use_existing: true, can_go_back: false },
  default_model: { id: "default_model", kind: "choose", prompt: "Default model", options: ["Codex · GPT-5.5"], default: 0, option_ids: ["codex:gpt-5.5"],
    supports_effort: true, default_effort: null, can_go_back: true },
  workspace: { id: "workspace", kind: "choose", prompt: "Which workspace?", options: ["Klovr", "Sign in to another workspace", "Save and exit"],
    option_ids: ["T0KLOVR1", "sign_in", "exit"], workspaces: [{ id: "T0KLOVR1", name: "Klovr", kind: "workspace" }], can_go_back: true },
  approve_setup: { id: "approve_setup", kind: "choose", prompt: "Ready?", options: ["Create in Slack", "Edit", "Edit AI", "Back"],
    option_ids: ["create", "edit", "edit_ai", "back"], can_go_back: true },
  channels: { id: "channels", kind: "multi", prompt: "Choose channels", options: ["#general"], selected: [], allow_empty: true, can_go_back: false },
  org: { id: "workspace", kind: "choose", prompt: "Which workspace?", options: ["Hover Tag Lab (organization)", "Sign in to another workspace", "Save and exit"],
    option_ids: ["E0LAB", "sign_in", "exit"], workspaces: [{ id: "E0LAB", name: "Hover Tag Lab", kind: "organization" }], can_go_back: true },
  org_workspace: { id: "org_workspace", kind: "choose", prompt: "Which workspace in Hover Tag Lab?", options: ["Workspace not listed?"],
    option_ids: ["manual"], workspaces: [], can_go_back: true },
  org_workspace_id: { id: "org_workspace_id", kind: "text", prompt: "Workspace address or ID (T…)", default: "", can_go_back: true },
  models: { id: "default_model", kind: "choose", prompt: "Default model", options: ["Codex · GPT-5.5", "Codex · GPT-5.5 mini"], default: 0,
    option_ids: ["codex:gpt-5.5", "codex:gpt-5.5-mini"], supports_effort: true, default_effort: "medium", can_go_back: true },
  ai_connection: { id: "ai_connection", kind: "choose", prompt: "Connect an AI in Settings", options: ["Check connections again"], option_ids: ["check"] },
};

/** A fake `tag add --json`: asks `order` in turn, pauses on a pause, and finishes with a result. */
function fakeSetup(order: string[], pausedTag: string | null = "new-tag") {
  const api = demoBridge();
  const sent: unknown[] = [];
  let emit: (line: string) => void = () => {};
  let exit: (code: number, stderr: string) => void = () => {};
  let at = 0;
  const say = (event: object) => act(() => emit(JSON.stringify(event)));
  const ask = (id: string) => say({ type: "question", ...QUESTIONS[id] });
  const finish = (status: string) => setTimeout(() => {
    const tag = status === "complete" ? "t0klovr1-a0new" : pausedTag;
    say({ type: "result", status, ...(tag ? { tag } : {}), exit_code: 0 });
    act(() => exit(0, ""));
  }, 0);
  api.setup = async (_args, onLine, onExit) => {
    emit = onLine;
    exit = onExit;
    setTimeout(() => ask(order[0]), 0);
    const session: Session = {
      send: (message) => {
        sent.push(message);
        const reply = message as { answer?: unknown; pause?: boolean };
        if (reply.pause) { finish("paused"); return; }
        if (reply.answer === "shuffle") return;
        if (reply.answer === "create") setTimeout(() => say({ type: "progress", step: "create", text: "Create the Slack app" }), 0);
        at += 1;
        if (at < order.length) setTimeout(() => ask(order[at]), 0);
        else finish("complete");
      },
      stop: () => {},
    };
    return session;
  };
  return { api, sent };
}

function open(order: string[], pausedTag?: string | null, enterprise?: string, modelsFrom?: string) {
  const { api, sent } = fakeSetup(order, pausedTag);
  const props = {
    start: vi.fn(async () => ""), close: vi.fn(), handOff: vi.fn(),
  };
  render(<QuickAdd api={api} workspace="T0KLOVR1" workspaceName="Klovr" enterprise={enterprise} modelsFrom={modelsFrom} {...props} />);
  return { sent, ...props };
}

const ALL = ["profile", "default_model", "workspace", "approve_setup", "channels"];

describe("Quick add", () => {
  it("asks for the name, description and picture, then creates and starts the Tag in place", async () => {
    const { sent, start, close } = open(ALL);
    expect(screen.getByRole("dialog", { name: "Add a Tag to Klovr" })).toBeTruthy();
    const name = await screen.findByLabelText("Name, as people mention it in Slack") as HTMLInputElement;
    await vi.waitFor(() => expect(name.value).toBe("Maya's Tag"));
    expect(screen.getByText("your account's default")).toBeTruthy();
    // One short form: no detour to the full setup.
    expect(screen.queryByRole("button", { name: "More options" })).toBeNull();
    fireEvent.change(name, { target: { value: "Docs Tag" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Finds answers in our docs" } });
    fireEvent.click(screen.getByRole("button", { name: "Create in Slack" }));

    expect(await screen.findByText("Docs Tag is ready")).toBeTruthy();
    expect(sent).toEqual([
      { answer: { name: "Docs Tag", description: "Finds answers in our docs" } },
      { answer: { value: "codex:gpt-5.5", effort: "default" } },
      { answer: "T0KLOVR1" }, { answer: "create" }, { answer: [] },
    ]);
    expect(start).toHaveBeenCalledWith("t0klovr1-a0new");
    // The finished Tag gets its moment: a hop and a check.
    expect(document.querySelector(".quick-made")).toBeTruthy();
    expect(document.querySelector(".quick-check")).toBeTruthy();
    expect(screen.getAllByText("Codex · GPT-5.5").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await vi.waitFor(() => expect(close).toHaveBeenCalledWith(true));
  });

  it("can't be dismissed while Slack is being changed", async () => {
    const { close } = open(["profile", "default_model"]);
    await screen.findByDisplayValue("Maya's Tag");
    fireEvent.click(screen.getByRole("button", { name: "Create in Slack" }));
    expect(await screen.findByLabelText("Installation progress")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(close).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
  });

  it("Cancel pauses setup and closes without adding a Tag", async () => {
    const { sent, close } = open(ALL);
    await screen.findByDisplayValue("Maya's Tag");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(sent).toEqual([{ answer: null, pause: true }]);
    await vi.waitFor(() => expect(close).toHaveBeenCalledWith(false));
  });

  it("hands a question it can't answer to the full setup", async () => {
    const { sent, handOff } = open(["profile", "ai_connection"]);
    await screen.findByDisplayValue("Maya's Tag");
    fireEvent.click(screen.getByRole("button", { name: "Create in Slack" }));
    await vi.waitFor(() => expect(handOff).toHaveBeenCalledWith("new-tag"));
    expect(sent.at(-1)).toEqual({ answer: null, pause: true });
  });
});

describe("Quick add in an Enterprise Grid organization", () => {
  it("picks the workspace's organization, then the workspace by its ID", async () => {
    const { sent, start } = open(["profile", "default_model", "org", "org_workspace", "org_workspace_id", "approve_setup", "channels"], undefined, "E0LAB");
    await screen.findByDisplayValue("Maya's Tag");
    fireEvent.click(screen.getByRole("button", { name: "Create in Slack" }));
    expect(await screen.findByText("Maya's Tag is ready")).toBeTruthy();
    expect(screen.getByText("Add it to Klovr")).toBeTruthy(); // As the full setup says for an organization.
    expect(sent.slice(2)).toEqual([{ answer: "E0LAB" }, { answer: "manual" }, { answer: "T0KLOVR1" }, { answer: "create" }, { answer: [] }]);
    expect(start).toHaveBeenCalled();
  });

  it("hands over rather than guess an organization it doesn't know", async () => {
    const { handOff } = open(["profile", "default_model", "org"]);
    await screen.findByDisplayValue("Maya's Tag");
    fireEvent.click(screen.getByRole("button", { name: "Create in Slack" }));
    await vi.waitFor(() => expect(handOff).toHaveBeenCalledWith("new-tag"));
  });
});

describe("Quick add without a Tag to continue", () => {
  it("says setup paused and can be closed, instead of waiting forever", async () => {
    const { handOff, close } = open(["profile", "ai_connection"], null);
    await screen.findByDisplayValue("Maya's Tag");
    fireEvent.click(screen.getByRole("button", { name: "Create in Slack" }));
    expect(await screen.findByText(/Setup stopped before it could finish/)).toBeTruthy();
    expect(handOff).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await vi.waitFor(() => expect(close).toHaveBeenCalledWith(false));
  });
});

describe("Choosing the model in the dialog", () => {
  it("lists the models through a Tag in the workspace and creates the Tag with the one picked", async () => {
    const { sent } = open(["profile", "models", "workspace", "approve_setup", "channels"], undefined, undefined, "t0klovr1-a0maya01");
    await screen.findByDisplayValue("Maya's Tag");
    fireEvent.click(screen.getByRole("button", { name: "Change" }));
    fireEvent.click(await screen.findByRole("button", { name: "Default model" }, { timeout: 3000 }));
    fireEvent.click(screen.getByRole("option", { name: /GPT-5\.5 mini/ }));
    expect(screen.getByRole("button", { name: "Default model" }).textContent).toContain("GPT-5.5 mini");
    fireEvent.click(screen.getByRole("button", { name: "Create in Slack" }));
    expect(await screen.findByText("Maya's Tag is ready")).toBeTruthy();
    // The picked model thinks at its own default level, not the default model's.
    expect(sent[1]).toEqual({ answer: { value: "codex:gpt-5.5-mini", effort: "default" } });
    expect(screen.getByText("Codex · GPT-5.5 mini")).toBeTruthy();
  });

  it("without a Tag in the workspace to ask, says where to change it later", async () => {
    open(ALL);
    await screen.findByDisplayValue("Maya's Tag");
    expect(screen.queryByRole("button", { name: "Change" })).toBeNull();
    expect(screen.getByText(/change it in its Details any time/)).toBeTruthy();
  });

  it("takes the default when setup doesn't offer the model picked", () => {
    const question = QUESTIONS.models as SetupQuestion;
    expect(quickAnswer(question, "T0KLOVR1", null, "claude:claude-opus-5-5")).toEqual({ answer: { value: "codex:gpt-5.5", effort: "medium" } });
    expect(quickAnswer(question, "T0KLOVR1", null, "codex:gpt-5.5-mini")).toEqual({ answer: { value: "codex:gpt-5.5-mini", effort: "default" } });
  });
});

describe("quickStep", () => {
  it("loads the models first, follows what Tag reports, then starts the Tag", () => {
    expect(quickStep("creating", false, [])).toBe(0);
    expect(quickStep("creating", true, [])).toBe(1);
    expect(quickStep("creating", true, ["create", "install"])).toBe(3);
    expect(quickStep("starting", true, ["connect"])).toBe(5);
    expect(quickStep("done", true, [])).toBe(6);
  });
});
