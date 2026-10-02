// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// The setup conversation as plain state, so the UI only draws what Tag asks.
import { parseSetupLine, type SetupQuestion } from "./protocol";

export type SignInStep = 0 | 1 | 2; // copy the line, send it in Slack, paste the code
export type Outcome = "complete" | "paused" | "failed";

export interface SetupState {
  question: SetupQuestion | null;
  signInStep: SignInStep;
  status: string;
  outcome: Outcome | null;
  error: string;
  tag: string;
}

export const initialSetup: SetupState = {
  question: null, signInStep: 0, status: "Starting…", outcome: null, error: "", tag: "",
};

export type SetupAction =
  | { type: "line"; line: string }
  | { type: "exit"; code: number; stderr: string }
  | { type: "answered" }
  | { type: "signIn"; step: SignInStep }
  | { type: "error"; message: string };

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
      if (event.type === "question") {
        const question = event as SetupQuestion;
        if (question.kind === "slack_login") {
          // Asked again: Slack refused the code, so stay on the code step.
          const again = state.question?.kind === "slack_login";
          return {
            ...state, question, signInStep: again ? 2 : 0,
            error: again ? "Slack didn't accept that code. Check it and try again." : "",
          };
        }
        return { ...state, question };
      }
      if (event.type === "result") {
        const result = event as { status: string; tag?: string; error?: string };
        const outcome: Outcome = result.status === "complete" || result.status === "paused" ? result.status : "failed";
        return { ...state, question: null, outcome, tag: result.tag ?? "", error: result.error ?? "" };
      }
      return state;
    }
    case "exit":
      return state.outcome ? state : { ...state, outcome: "failed", question: null, error: explainExit(action.stderr) };
    case "answered":
      // Keep the sign-in question up while Slack checks the code; it may come back.
      return state.question?.kind === "slack_login"
        ? state
        : { ...state, question: null, status: "Loading…", error: "" };
    case "signIn":
      return { ...state, signInStep: action.step, error: action.step === 2 ? state.error : "" };
    case "error":
      return { ...state, error: action.message };
  }
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
    default:
      return [question.prompt, question.kind === "secret" ? "Stays on this computer." : ""];
  }
}

/** Setup offers an exit option in lists; the app has its own Cancel. */
export const EXIT_OPTION = "Exit · finish setup later";
