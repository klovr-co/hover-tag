// Add a Tag follows setup's own order: Your Tag, AI, Workspace, Create, Channels.
import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { demoBridge, type Session } from "../lib/bridge";
import { TrackContext, type Track } from "../lib/telemetry";
import { Connect } from "./Connect";
import { workspaceIdError } from "./Connect";

vi.mock("@tauri-apps/api/core", () => ({ convertFileSrc: (path: string) => `asset://localhost${encodeURIComponent(path)}` }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

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

function setup(track: Track = () => {}) {
  const api = demoBridge();
  const answers: unknown[] = [];
  let emit: (line: string) => void = () => {};
  let at = 0;
  const ask = (id: string) => act(() => emit(JSON.stringify({ type: "question", ...QUESTIONS[id] })));
  api.setup = async (_args, onLine, onExit) => {
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
        else setTimeout(() => act(() => {
          emit(JSON.stringify({ type: "result", status: "complete", tag: "t1",
            ready: { team: "T0KLOVR1", app_id: "A1", channels: [{ id: "C1", name: "general" }], ai: { backend: "codex", backend_name: "Codex", label: "GPT-5.5" } } }));
          onExit(0, "");
        }), 0);
      },
      stop: () => {},
    };
    return session;
  };
  render(<StrictMode><TrackContext.Provider value={track}>
    <Connect api={api} args={["setup"]} done={vi.fn()} paused={vi.fn()} />
  </TrackContext.Provider></StrictMode>);
  return answers;
}

const step = () => document.querySelector(".tstep.now")?.textContent;

it("shows setup workspace and owner pictures, falls back on failure, and retries new sources", async () => {
  vi.stubGlobal("__TAURI_INTERNALS__", {});
  const api = demoBridge();
  let emit!: (line: string) => void;
  api.setup = async (_args, onLine) => { emit = onLine; return { send: vi.fn(), stop: vi.fn() }; };
  render(<Connect api={api} args={["add"]} done={vi.fn()} paused={vi.fn()} />);
  await vi.waitFor(() => expect(emit).toBeTypeOf("function"));
  const recap = (QUESTIONS.approve_setup as { recap: object }).recap;
  const ask = (suffix?: string) => act(() => emit(JSON.stringify({ type: "question", ...QUESTIONS.approve_setup,
    recap: { ...recap,
      workspace: { id: "T1", name: "Klovr", organization: { id: "E1", name: "Parent org" }, ...(suffix ? { icon: `/team-${suffix}.png` } : {}) },
      owner: { id: "U1", name: "maya", ...(suffix ? { icon: `/owner-${suffix}.png` } : {}) },
    },
  })));
  ask(); // Older runtimes and first-time connections have no image fields.
  expect(document.querySelector(".summary .ws")?.textContent).toBe("K");
  expect(document.querySelector(".summary .you svg")).toBeTruthy();
  for (const suffix of ["first", "refreshed"]) {
    ask(suffix);
    const team = document.querySelector<HTMLImageElement>(".summary img.ws")!;
    const owner = document.querySelector<HTMLImageElement>(".summary .you img")!;
    expect(team.getAttribute("src")).toContain(encodeURIComponent(`/team-${suffix}.png`));
    expect(owner.getAttribute("src")).toContain(encodeURIComponent(`/owner-${suffix}.png`));
    fireEvent.error(team);
    fireEvent.error(owner);
    expect(document.querySelector(".summary .ws")?.textContent).toBe("K");
    expect(document.querySelector(".summary .you svg")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Create in Slack" }).hasAttribute("disabled")).toBe(false);
  }
});

it("saves setup thinking with the model and fits it when switching providers", async () => {
  const api = demoBridge();
  let emit!: (line: string) => void;
  const send = vi.fn();
  api.setup = async (_args, onLine) => { emit = onLine; return { send, stop: vi.fn() }; };
  render(<Connect api={api} args={["add"]} done={vi.fn()} paused={vi.fn()} />);
  await vi.waitFor(() => expect(emit).toBeTypeOf("function"));
  act(() => emit(JSON.stringify({ type: "question", ...QUESTIONS.default_model, supports_effort: true,
    default_effort: "high", option_ids: ["codex:gpt-5.5", "claude:opus"],
    connections: [codex, { ...codex, backend: "claude", name: "Claude" }],
    groups: [
      { backend: "codex", name: "Codex", models: [{ value: "codex:gpt-5.5", label: "GPT-5.5", efforts: ["low", "high"], default_effort: "low" }] },
      { backend: "claude", name: "Claude", models: [{ value: "claude:opus", label: "Opus", efforts: ["low", "max"], default_effort: "max" }] },
    ],
  })));
  expect(screen.getByRole("radio", { name: "High" }).getAttribute("aria-checked")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "Default model" }));
  fireEvent.click(screen.getByRole("option", { name: "Opus" }));
  expect(screen.queryByRole("radio", { name: "High" })).toBeNull();
  expect(screen.getByRole("radio", { name: /Max/ }).getAttribute("aria-checked")).toBe("true");
  fireEvent.click(screen.getByRole("radio", { name: "Low" }));
  fireEvent.click(screen.getByRole("button", { name: /Continue/ }));
  expect(send).toHaveBeenLastCalledWith({ answer: { value: "claude:opus", effort: "low" } });
});

it("reloads pictures replaced at the same path and keeps the selected picture in Create", async () => {
  vi.stubGlobal("__TAURI_INTERNALS__", {});
  const api = demoBridge();
  let emit!: (line: string) => void;
  const send = vi.fn();
  api.setup = async (_args, onLine) => { emit = onLine; return { send, stop: vi.fn() }; };
  render(<Connect api={api} args={["add"]} done={vi.fn()} paused={vi.fn()} />);
  await vi.waitFor(() => expect(emit).toBeTypeOf("function"));
  const path = "/Users/maya/Tag/.tag/assets/tag-profile.png";
  const ask = (revision: string) => act(() => emit(JSON.stringify({ type: "question", ...QUESTIONS.profile,
    preview: path, preview_revision: revision, picture_label: "face.png" })));
  const picture = () => screen.getByAltText("Maya's Tag's picture").getAttribute("src");
  ask("first");
  const first = picture();
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "My description" } });
  expect(picture()).toBe(first);
  for (const revision of ["second", "third"]) {
    const previous = picture();
    fireEvent.click(screen.getByRole("button", { name: /Shuffle picture/ }));
    expect(send).toHaveBeenLastCalledWith({ answer: "shuffle" });
    expect(screen.getByRole("button", { name: /Shuffle picture/ }).hasAttribute("disabled")).toBe(true);
    ask(revision);
    expect(picture()).not.toBe(previous);
    expect(picture()).toContain(`?v=${revision}`);
    expect(document.querySelector(".tnode img")?.getAttribute("src")).toBe(picture());
    expect((screen.getByLabelText("Description") as HTMLTextAreaElement).value).toBe("My description");
  }
  const selected = picture();
  act(() => emit(JSON.stringify({ type: "question", ...QUESTIONS.approve_setup,
    recap: { ...(QUESTIONS.approve_setup as { recap: object }).recap, picture: path, picture_revision: "third" } })));
  const pictures = [...document.querySelectorAll("img")].map((img) => img.getAttribute("src"));
  expect(pictures.filter((src) => src === selected)).toHaveLength(2);
});

describe("Add a Tag", () => {
  it("asks for the name, picture and description first, then AI, workspace, Create and channels", async () => {
    const track = vi.fn();
    const answers = setup(track);
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
    // Creating shows only the installation, straight away: no recap, no choices.
    expect(screen.getByText("Adding Maya's Tag to Slack…")).toBeTruthy();
    expect(screen.getByLabelText("Installation progress")).toBeTruthy();
    expect(screen.queryByText("Who can ask it")).toBeNull();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();

    expect(await screen.findByText("Where should Maya's Tag start?")).toBeTruthy();
    expect(step()).toBe("Channels");
    fireEvent.click(screen.getByRole("checkbox", { name: /launch/ }));
    fireEvent.click(screen.getByRole("button", { name: "Continue with 2 channels" }));
    expect(answers[5]).toEqual([0, 1]);

    expect(await screen.findByText("Say hi to Maya's Tag")).toBeTruthy();
    expect(screen.getByText("Codex connected")).toBeTruthy();
    expect(screen.queryByText(/replied/)).toBeNull();

    // Usage data follows the step track once, even under StrictMode, and never carries answers.
    expect(track.mock.calls.map(([event, fields]) => event === "app_setup_step_completed" ? fields.step : event)).toEqual([
      "app_setup_started", "tag", "ai", "workspace", "create", "channels", "app_setup_completed",
    ]);
    expect(track).toHaveBeenCalledWith("app_setup_completed", { entry_point: "first_tag", elapsed_seconds: expect.any(Number) });
    expect(JSON.stringify(track.mock.calls)).not.toMatch(/Maya|Klovr|Launch help|T0KLOVR1/);
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

it("waits for setup to save before opening Settings and resumes the same new Tag", async () => {
  const api = demoBridge();
  let emit: (line: string) => void = () => {};
  const send = vi.fn();
  api.setup = async (_args, onLine) => {
    emit = onLine;
    setTimeout(() => emit(JSON.stringify({ type: "question", id: "ai_connection", kind: "choose",
      prompt: "Connect an AI in Settings", options: ["Check connections again"], option_ids: ["check"],
      connections: [], can_continue: false })), 0);
    return { send, stop: vi.fn() };
  };
  const openAI = vi.fn();
  render(<Connect api={api} args={["add"]} done={vi.fn()} paused={vi.fn()} openAI={openAI} />);
  fireEvent.click(await screen.findByRole("button", { name: "Open Settings" }));
  expect(send).toHaveBeenCalledWith({ answer: null, pause: true });
  expect(openAI).not.toHaveBeenCalled();
  act(() => emit(JSON.stringify({ type: "result", status: "paused", tag: "new-tag-2" })));
  await vi.waitFor(() => expect(openAI).toHaveBeenCalledWith(["new-tag-2", "setup"]));
});


it("starts only one setup process under StrictMode and stops it on unmount", async () => {
  const api = demoBridge();
  const stop = vi.fn();
  api.setup = vi.fn(async () => ({ send: vi.fn(), stop }));
  const view = render(<StrictMode><Connect api={api} args={["add"]} done={vi.fn()} paused={vi.fn()} /></StrictMode>);
  expect(step()).toBe("Your Tag");
  expect(screen.getByRole("status").textContent).toBe("Preparing setup…");
  await vi.waitFor(() => expect(api.setup).toHaveBeenCalledTimes(1));
  view.unmount();
  expect(stop).toHaveBeenCalledTimes(1);
});

it("shows a whole channel setup status until the next complete update", async () => {
  const api = demoBridge();
  let emit!: (line: string) => void;
  api.setup = vi.fn(async (_args, onLine) => { emit = onLine; return { send: vi.fn(), stop: vi.fn() }; });
  render(<Connect api={api} args={["add"]} done={vi.fn()} paused={vi.fn()} />);
  await vi.waitFor(() => expect(api.setup).toHaveBeenCalledTimes(1));
  act(() => emit(JSON.stringify({ type: "question", ...QUESTIONS.channels })));
  fireEvent.click(screen.getByRole("button", { name: "Continue with 1 channel" }));
  const text = "✓ Slack connected\n◌ Preparing Slack memory for the selected channels…";
  act(() => emit(JSON.stringify({ type: "message", text })));
  expect(screen.getByRole("status").textContent).toBe(text);
  act(() => emit(JSON.stringify({ type: "message", text: "Memory ready" })));
  expect(screen.getByRole("status").textContent).toBe("Memory ready");
});

it("stops a late setup session after the screen closes", async () => {
  const api = demoBridge();
  let finish!: (session: Session) => void;
  api.setup = vi.fn(() => new Promise<Session>((resolve) => { finish = resolve; }));
  const view = render(<Connect api={api} args={["add"]} done={vi.fn()} paused={vi.fn()} />);
  await vi.waitFor(() => expect(api.setup).toHaveBeenCalledTimes(1));
  view.unmount();
  const stop = vi.fn();
  await act(async () => finish({ send: vi.fn(), stop }));
  expect(stop).toHaveBeenCalledTimes(1);
});

it("offers retry if the setup process cannot start", async () => {
  const api = demoBridge();
  api.setup = vi.fn().mockRejectedValueOnce(new Error("Couldn't start Tag"))
    .mockResolvedValue({ send: vi.fn(), stop: vi.fn() });
  render(<Connect api={api} args={["add"]} done={vi.fn()} paused={vi.fn()} />);
  expect(await screen.findByText("Setup stopped")).toBeTruthy();
  expect(step()).toBe("Your Tag");
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await vi.waitFor(() => expect(api.setup).toHaveBeenCalledTimes(2));
  expect(screen.queryByText("Setup stopped")).toBeNull();
});

it("retains the failed step and retries the reported Tag instead of adding another", async () => {
  const api = demoBridge();
  let emit!: (line: string) => void;
  api.setup = vi.fn(async (_args, onLine) => { emit = onLine; return { send: vi.fn(), stop: vi.fn() }; });
  render(<Connect api={api} args={["add"]} done={vi.fn()} paused={vi.fn()} />);
  await vi.waitFor(() => expect(api.setup).toHaveBeenCalledTimes(1));
  act(() => {
    emit(JSON.stringify({ type: "question", ...QUESTIONS.channels }));
    emit(JSON.stringify({ type: "result", status: "failed", tag: "new-tag", error: "Connection lost" }));
  });
  expect(step()).toBe("Channels");
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await vi.waitFor(() => expect(api.setup).toHaveBeenCalledTimes(2));
  expect(vi.mocked(api.setup).mock.calls[1][0]).toEqual(["new-tag", "setup"]);
});

it("greets the Tag by the name it was given and starts it without a click", async () => {
  const api = demoBridge();
  let emit: (line: string) => void = () => {};
  const started = vi.fn(async () => ({ code: 0, stdout: "", stderr: "" }));
  api.tag = started as typeof api.tag;
  let exit: (code: number, stderr: string) => void = () => {};
  api.setup = async (_args, onLine, onExit) => {
    emit = onLine;
    exit = onExit;
    setTimeout(() => act(() => emit(JSON.stringify({ type: "question", ...QUESTIONS.profile }))), 0);
    return { send: () => setTimeout(() => act(() => {
      emit(JSON.stringify({ type: "result", status: "complete", tag: "t1",
        ready: { team: "T1", app_id: "A1", channels: [], ai: null, owner: { id: "U1", name: "maya", icon: null } } }));
      exit(0, "");
    }), 0), stop: () => {} };
  };
  render(<Connect api={api} args={["setup"]} done={() => {}} paused={() => {}} />);
  fireEvent.change(await screen.findByLabelText("Name, as people mention it in Slack"), { target: { value: "Nova" } });
  fireEvent.click(screen.getByRole("button", { name: /Continue/ }));
  expect(await screen.findByText("Say hi to Nova")).toBeTruthy();
  expect(screen.getByText("maya")).toBeTruthy();
  await vi.waitFor(() => expect(started).toHaveBeenCalledWith(["t1", "start"]));
  await vi.waitFor(() => expect(screen.getByRole("button", { name: /Open Slack/ }).hasAttribute("disabled")).toBe(false));
});

it("shows each start step as it happens, then the message to send", async () => {
  const api = demoBridge();
  let emit: (line: string) => void = () => {};
  let exit: (code: number, stderr: string) => void = () => {};
  api.setup = async (_args, onLine, onExit) => {
    emit = onLine;
    exit = onExit;
    setTimeout(() => act(() => {
      emit(JSON.stringify({ type: "result", status: "complete", tag: "t1",
        ready: { name: "Nova", team: "T1", app_id: "A1", channels: [{ id: "C1", name: "prod-hover" }], ai: null } }));
      exit(0, "");
    }), 0);
    return { send: () => {}, stop: () => {} };
  };
  let report: (line: string) => void = () => {};
  let finish: (failure: string) => void = () => {};
  const start = vi.fn((_tag: string, onLine?: (line: string) => void) => {
    report = onLine ?? (() => {});
    return new Promise<string>((resolve) => { finish = resolve; });
  });
  render(<Connect api={api} args={["setup"]} done={() => {}} paused={() => {}} start={start} />);
  expect(await screen.findByText("Starting Nova…")).toBeTruthy();
  expect(start).toHaveBeenCalledWith("t1", expect.any(Function));
  act(() => {
    report('{"type": "progress", "step": "memory", "label": "Memory", "state": "done", "text": "Healthy"}');
    report('{"type": "progress", "step": "channel-memory", "label": "Channel memory", "state": "running", "text": "Waiting…"}');
  });
  expect(screen.getByText("Reading its channels").closest(".st")?.className).toContain("running");
  expect(screen.getByText("Starting memory").closest(".st")?.className).toContain("done");
  expect(screen.getByText(/Reading recent messages in #prod-hover/)).toBeTruthy();
  // The message to send waits until the Tag can answer it.
  expect(screen.queryByText("What can you help me with?")).toBeNull();
  expect(screen.getByRole("button", { name: /Open Slack/ }).hasAttribute("disabled")).toBe(true);

  await act(async () => finish("MFS scope did not become readable after indexing"));
  expect(screen.getByText("Nova didn't start")).toBeTruthy();
  expect(screen.getByText("Reading its channels").closest(".st")?.className).toContain("failed");
  expect(screen.getByText("MFS scope did not become readable after indexing")).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await vi.waitFor(() => expect(start).toHaveBeenCalledTimes(2));
  act(() => report('{"type": "progress", "step": "channel-memory", "label": "Channel memory", "state": "info", "text": "Importing #prod-hover in the background"}'));
  await act(async () => finish(""));
  expect(screen.getByText("Say hi to Nova")).toBeTruthy();
  expect(screen.getByText("What can you help me with?")).toBeTruthy();
  // It answers now; history search catches up.
  expect(screen.getByText(/Nova can answer now. It's still reading older messages in #prod-hover/)).toBeTruthy();
});
