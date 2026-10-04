// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// The setup conversation as plain state, so the UI only draws what Tag asks.
import { idleSignIn, signInReducer, type SignInState } from "./ai";
import { parseSetupLine, type SetupQuestion, type SetupReady } from "./protocol";

export type SignInStep = 0 | 1 | 2; // copy the line, send it in Slack, paste the code
export type Outcome = "complete" | "paused" | "failed";

/** The steps the onboarding track shows, for a new app and for an existing one. */
export const FLOW = ["Your Tag", "AI", "Workspace", "Create", "Channels"];
export const EXISTING_FLOW = ["AI", "Workspace", "Your app", "Channels"];

export interface SetupState {
  question: SetupQuestion | null;
  signInStep: SignInStep;
  status: string;
  outcome: Outcome | null;
  error: string;
  tag: string;
  /** An agent sign-in started from the AI step, while it runs and after it ends. */
  signIn: SignInState;
  /** Creating the Slack app: the steps reported so far, the last one running. */
  creating: { step: string; text: string }[] | null;
  /** The finished Tag's Slack IDs and channels, for the Ready screen. */
  ready: SetupReady | null;
  /** Setting up with an app the person already has. */
  existing: boolean;
  /** The last workspace list, so an organization opens inside it. */
  workspaces: SetupQuestion | null;
  /** The last profile, so later steps show the Tag's name and picture. */
  profile: { name: string; preview: string | null } | null;
  /** Slack sign-in came from "Sign in to another workspace". */
  addingWorkspace: boolean;
}

export const initialSetup: SetupState = {
  question: null, signInStep: 0, status: "Starting…", outcome: null, error: "", tag: "", signIn: idleSignIn,
  creating: null, ready: null, existing: false, workspaces: null, profile: null, addingWorkspace: false,
};

export type SetupAction =
  | { type: "line"; line: string }
  | { type: "exit"; code: number; stderr: string }
  | { type: "answered" }
  /** The AI step answered with a sign-in: keep the question up and show progress. */
  | { type: "agentSignIn"; backend: string }
  | { type: "signIn"; step: SignInStep }
  | { type: "existing" }
  | { type: "addWorkspace" }
  | { type: "error"; message: string };

const EXISTING_QUESTIONS = new Set(["existing_app", "app_id", "app_checks"]);

/** A setup process that ended without a result, as something a person can act on. */
export function explainExit(stderr: string): string {
  const last = stderr.trim().split("\n").pop() ?? "";
  if (last.includes("--json supports")) {
    return "This version of Tag can't be set up from the app yet. Update Tag, then try again.";
  }
  if (!last) return "Setup stopped unexpectedly. Run tag setup in a terminal to see why.";
  return last.replace("tag_cli.py: error: ", "");
}

export function setupReducer(state: SetupState, action: SetupAction): SetupState {
  switch (action.type) {
    case "line": {
      const event = parseSetupLine(action.line);
      if (!event) return state;
      if (event.type === "message") return { ...state, status: (event as { text: string }).text || state.status };
      if (event.type === "progress" && !("backend" in event && event.backend)) {
        const step = event as { step: string; text: string };
        const done = (state.creating ?? []).filter((s) => s.step !== step.step);
        return { ...state, question: null, creating: [...done, { step: step.step, text: step.text }] };
      }
      if (event.type === "progress" || event.type === "sign_in") {
        return { ...state, signIn: signInReducer(state.signIn, { type: "line", line: action.line }) };
      }
      if (event.type === "question") {
        const question = event as SetupQuestion;
        const existing = state.existing || EXISTING_QUESTIONS.has(question.id);
        const workspaces = question.id === "workspace" ? question : state.workspaces;
        const profile = question.id === "profile" ? { name: question.name ?? "", preview: question.preview ?? null } : state.profile;
        const base = { ...state, existing, workspaces, profile, creating: null };
        if (question.kind === "slack_login") {
          // Asked again: Slack refused the code, so stay on the code step.
          const again = state.question?.kind === "slack_login";
          return {
            ...base, question, signInStep: again ? 2 : 0,
            error: again ? "Slack didn't accept that code. Check it and try again." : "",
          };
        }
        // A sign-in ends when the AI step is asked again; keep its outcome on screen.
        const signIn = state.signIn.step ? { ...state.signIn, step: null } : state.signIn;
        return {
          ...base, question, signIn: ["ai_connection", "default_model"].includes(question.id) ? signIn : idleSignIn,
          addingWorkspace: question.id === "workspace" ? false : state.addingWorkspace,
          // A question asked again with its own error says what's wrong; keep it, not the last one.
          error: "",
        };
      }
      if (event.type === "result") {
        const result = event as { status: string; tag?: string; error?: string; ready?: SetupReady };
        const outcome: Outcome = result.status === "complete" || result.status === "paused" ? result.status : "failed";
        return { ...state, question: null, creating: null, outcome, tag: result.tag ?? "", error: result.error ?? "", ready: result.ready ?? null };
      }
      return state;
    }
    case "exit":
      return state.outcome ? state : { ...state, outcome: "failed", question: null, creating: null, error: explainExit(action.stderr) };
    case "answered":
      // Keep the sign-in question up while Slack checks the code; it may come back.
      return state.question?.kind === "slack_login"
        ? state
        : { ...state, question: null, status: "Loading…", error: "" };
    case "agentSignIn":
      return { ...state, signIn: signInReducer(idleSignIn, { type: "start", backend: action.backend }), error: "" };
    case "signIn":
      return { ...state, signInStep: action.step, error: action.step === 2 ? state.error : "" };
    case "existing":
      return { ...state, existing: true };
    case "addWorkspace":
      return { ...state, addingWorkspace: true };
    case "error":
      return { ...state, error: action.message };
  }
}

/** Where the track's marker stands for a question, by flow. */
export function trackStep(state: SetupState): number {
  const id = state.question?.id ?? "";
  if (state.existing) {
    if (["ai_connection", "default_model"].includes(id)) return 0;
    if (EXISTING_QUESTIONS.has(id)) return 2;
    if (id === "channels") return 3;
    return state.outcome === "complete" ? 4 : 1;
  }
  if (id === "profile") return 0;
  if (["ai_connection", "default_model"].includes(id)) return 1;
  if (id === "approve_setup" || state.creating) return 3;
  if (id === "channels") return 4;
  if (state.outcome === "complete") return 5;
  return 2;
}

/** Friendlier wording for questions the app knows; anything else shows Tag's own. */
export function heading(question: SetupQuestion): [string, string] {
  switch (question.id) {
    case "workspace":
      return ["Which workspace?", "Pick the Slack workspace this Tag will work in."];
    case "channels":
      return ["Where should Tag respond?", "Tag answers and remembers conversations in these channels."];
    case "approve_setup":
      return ["Ready to create Tag's Slack app?", "Continue to create and install it, or go back to change your choices."];
    case "create_app":
      return ["Create the Slack app", "Slack creates and installs Tag's app in this workspace."];
    case "ai_connection":
      return [`Connect ${question.tag_name || "Tag"} to an AI`, "Set up Codex or Claude on this Mac to continue."];
    case "default_model":
      return [`Choose ${question.tag_name || "Tag"}'s model`, "From the AI accounts on this Mac. Change it any time in Settings."];
    default:
      return [question.prompt, question.kind === "secret" ? "Stays on this computer." : ""];
  }
}

/** Setup offers an exit option in lists; the app has its own Cancel. */
export const EXIT_OPTION = "Exit · finish setup later";
