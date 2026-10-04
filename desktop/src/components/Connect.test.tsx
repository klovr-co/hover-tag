// Add a Tag follows setup's own order: Your Tag, AI, Workspace, Create, Channels.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { demoBridge, type Session } from "../lib/bridge";
import { Connect } from "./Connect";
import { workspaceIdError } from "./Connect";

afterEach(cleanup);

const codex = { backend: "codex", name: "Codex", provider: "OpenAI models", state: "connected", installed: true, method: "codex",
  account: "ChatGPT sign-in", detail: "", shared: true, actions: ["change_account"], install_url: "https://learn.chatgpt.com/docs/codex/cli", allowed: true };
const QUESTIONS: Record<string, object> = {
  profile: { id: "profile", kind: "profile_picture", prompt: "Meet your new Tag", name: "Maya's Tag", name_limit: 35, description: "",
    description_limit: 140, preview: null, picture: "waterdrop", error: null, editing: false, can_use_existing: true, can_go_back: false },
  default_model: { id: "default_model", kind: "choose", prompt: "Default model", options: ["Codex · GPT-5.5"], default: 0, option_ids: ["codex:gpt-5.5"],
    groups: [{ backend: "codex", name: "Codex", models: [{ value: "codex:gpt-5.5", model: "gpt-5.5", label: "GPT-5.5", default: true }] }],
    connections: [codex], tag_name: "Maya's Tag", can_go_back: true },
  workspace: { id: "workspace", kind: "choose", prompt: "Which workspace?", options: ["Klovr", "Sign in to another workspace", "Save and exit"],
    option_ids: ["T0KLOVR1", "sign_in", "exit"], workspaces: [{ id: "T0KLOVR1", name: "Klovr", kind: "workspace", user_id: "U1", user_name: "maya" }], can_go_back: true },
  approve_setup: { id: "approve_setup", kind: "choose", prompt: "Ready?", options: ["Create in Slack", "Edit", "Edit AI", "Back"],
    option_ids: ["create", "edit", "edit_ai", "back"], can_go_back: true,
    recap: { name: "Maya's Tag", description: "Launch help", picture: null, workspace: { id: "T0KLOVR1", name: "Klovr", organization: null },
      owner: { id: "U1", name: "maya" }, ai: { value: "codex:gpt-5.5", backend: "codex", backend_name: "Codex", label: "GPT-5.5" }, approval: false } },
  channels: { id: "channels", kind: "multi", prompt: "Choose channels", options: ["#general", "#launch"], selected: [0], allow_empty: true, can_go_back: true,
    channels: [{ id: "C1", name: "general", member: true, private: false }, { id: "C2", name: "launch", member: false, private: false }] },
};
const ORDER = ["profile", "default_model", "workspace", "approve_setup", "channels"];

function setup() {
  const api = demoBridge();
  const answers: unknown[] = [];
  let emit: (line: string) => void = () => {};
  let at = 0;
  const ask = (id: string) => act(() => emit(JSON.stringify({ type: "question", ...QUESTIONS[id] })));
  api.setup = async (_args, onLine) => {
    emit = onLine;
    setTimeout(() => ask(ORDER[0]), 0);
    const session: Session = {
      send: (message) => {
        const answer = (message as { answer?: unknown }).answer;
        answers.push(answer);
        if (answer === "shuffle") { setTimeout(() => ask("profile"), 0); return; }
        if (answer === "create") {
          setTimeout(() => act(() => { emit(JSON.stringify({ type: "progress", step: "create", text: "Create the Slack app" })); }), 0);
          setTimeout(() => ask("channels"), 5);
          at = ORDER.indexOf("channels");
          return;
        }
        at += 1;
        if (at < ORDER.length) setTimeout(() => ask(ORDER[at]), 0);
        else setTimeout(() => act(() => emit(JSON.stringify({ type: "result", status: "complete", tag: "t1",
          ready: { team: "T0KLOVR1", app_id: "A1", channels: [{ id: "C1", name: "general" }], ai: { backend: "codex", backend_name: "Codex", label: "GPT-5.5" } } }))), 0);
      },
      stop: () => {},
    };
    return session;
  };
  render(<Connect api={api} args={["setup"]} done={vi.fn()} paused={vi.fn()} />);
  return answers;
}

const step = () => document.querySelector(".tstep.now")?.textContent;

describe("Add a Tag", () => {
  it("asks for the name, picture and description first, then AI, workspace, Create and channels", async () => {
    const answers = setup();
    expect(await screen.findByText("Meet your new Tag")).toBeTruthy();
    expect(step()).toBe("Your Tag");
    // Shuffle answers in place; the screen stays.
    fireEvent.click(screen.getByRole("button", { name: /Shuffle picture/ }));
    expect(answers).toEqual(["shuffle"]);
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Launch help" } });
    await vi.waitFor(() => expect(screen.getByRole("button", { name: /Continue/ }).hasAttribute("disabled")).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: /Continue/ }));
    expect(answers[1]).toEqual({ name: "Maya's Tag", description: "Launch help" });

    expect(await screen.findByText("Choose Maya's Tag's model")).toBeTruthy();
    expect(step()).toBe("AI");
    fireEvent.click(screen.getByRole("button", { name: /Continue/ }));
    expect(answers[2]).toBe("codex:gpt-5.5");

    expect(await screen.findByText("Which workspace?")).toBeTruthy();
    expect(step()).toBe("Workspace");
    expect(screen.getByText("You're already signed in to these. You'll own Maya's Tag.")).toBeTruthy();
    fireEvent.click(screen.getByRole("option", { name: /Klovr/ }));
    expect(answers[3]).toBe("T0KLOVR1");

    expect(await screen.findByText("Ready to create it in Klovr?")).toBeTruthy();
    expect(step()).toBe("Create");
    expect(screen.getByText(/You · @maya/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Create in Slack" }));
    expect(answers[4]).toBe("create");

    expect(await screen.findByText("Where should Maya's Tag start?")).toBeTruthy();
    expect(step()).toBe("Channels");
    fireEvent.click(screen.getByRole("checkbox", { name: /launch/ }));
    fireEvent.click(screen.getByRole("button", { name: "Continue with 2 channels" }));
    expect(answers[5]).toEqual([0, 1]);

    expect(await screen.findByText("Say hi to Maya's Tag")).toBeTruthy();
    expect(screen.getByText("Codex connected")).toBeTruthy();
    expect(screen.queryByText(/replied/)).toBeNull();
  });

  it("never asks which person you are", () => {
    expect(Object.values(QUESTIONS).some((q) => (q as { kind: string }).kind === "people")).toBe(false);
  });

  it("explains a pasted organization ID", () => {
    expect(workspaceIdError("E0C6K31U308")).toBe("That's the organization ID. Use the workspace's, which starts with T.");
    expect(workspaceIdError("hover-eng.slack.com")).toBe("No workspace ID found. It starts with T.");
    expect(workspaceIdError("https://app.slack.com/client/T0C7R1B44/C1")).toBe("");
  });
});
