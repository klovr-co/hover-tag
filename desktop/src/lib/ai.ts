// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// The app's side of "AI connections" in docs/reference/app-protocol.md: which
// agents a Tag can use, its default model, and browser sign-in. Tag does the
// checks and sign-ins; this file only reads what `tag … settings ai` prints.
import { parseJSON } from "./protocol";

export const AI_CAPABILITY = "ai-connections";

export type ConnectionState =
  | "connected" | "signed_out" | "expired" | "limited" | "not_installed" | "unsupported" | string;
export type ConnectionAction = "sign_in" | "reconnect" | "change_account" | "install" | "resume" | "update" | string;

export interface Connection {
  backend: "codex" | "claude" | string;
  name: string;
  provider: string;
  state: ConnectionState;
  installed: boolean;
  version?: string | null;
  /** codex: the computer's Codex sign-in; chatgpt: a ChatGPT plan for this Tag only. */
  method?: "codex" | "chatgpt" | "claude" | string | null;
  account?: string | null;
  detail?: string;
  /** True when the sign-in belongs to the computer, not just this Tag. */
  shared: boolean;
  actions: ConnectionAction[];
  install_url: string;
  allowed?: boolean;
}

export interface ModelChoice {
  value: string;
  backend: string;
  model: string | null;
  label: string;
  backend_name: string;
  available: boolean | null;
  chosen?: boolean;
}

export interface AIStatus {
  tag: string;
  running: boolean;
  connections: Connection[];
  usable: string[];
  default_model: ModelChoice;
  checked_at?: string;
}

export interface ModelEntry {
  value: string;
  model: string | null;
  label: string;
  default: boolean;
}

export interface ModelGroup {
  backend: string;
  name: string;
  models: ModelEntry[];
}

export interface AIModels {
  groups: ModelGroup[];
  default: ModelChoice;
  unavailable: { backend: string; name: string; reason: string }[];
  suggested: string | null;
}

export type SignInStep = "stopping" | "browser" | "waiting" | "verifying" | "restarting" | string;

export interface SignInResult {
  type: "sign_in";
  backend: string;
  status: "connected" | "cancelled" | "failed" | string;
  connection?: Connection;
  error?: string;
  retry?: boolean;
  restarted?: boolean;
  restart_required?: boolean;
}

export type SignInEvent =
  | { type: "progress"; backend: string; step: SignInStep; text: string; url?: string }
  | SignInResult;

export const parseStatus = (output: string) => parseJSON<AIStatus>(output);
export const parseModels = (output: string) => parseJSON<AIModels>(output);

/** One stdout line from a sign-in; anything else is ignored. */
export function parseSignInLine(line: string): SignInEvent | { type: "error"; error: string } | null {
  const text = line.trim();
  if (!text.startsWith("{")) return null;
  try {
    const event = JSON.parse(text);
    if (event?.type === "progress" || event?.type === "sign_in") return event as SignInEvent;
    // A command that failed before starting prints {"ok": false, "error": …}.
    if (event?.ok === false && typeof event.error === "string") return { type: "error", error: event.error };
  } catch {
    // Not one of ours.
  }
  return null;
}

// ---- Words -------------------------------------------------------------------

export type Tone = "good" | "muted" | "warn" | "bad" | "busy";

const LABEL: Record<string, string> = {
  signed_out: "Not signed in",
  expired: "Sign-in expired",
  limited: "Usage limit reached",
  not_installed: "Not installed",
  unsupported: "Update needed",
};

/** The status line under a backend's name, e.g. "Connected · ChatGPT sign-in". */
export function statusLine(connection: Connection): { text: string; tone: Tone } {
  if (connection.state === "connected") {
    return { text: `Connected${connection.account ? ` · ${connection.account}` : ""}`, tone: "good" };
  }
  const label = LABEL[connection.state] ?? "Needs attention";
  const tone: Tone = ["expired", "limited", "unsupported"].includes(connection.state) ? "warn" : "muted";
  return { text: connection.state === "unsupported" && connection.detail ? `${label} · ${connection.detail}` : label, tone };
}

export const ACTION_LABEL: Record<string, string> = {
  sign_in: "Sign in",
  reconnect: "Reconnect",
  change_account: "Change account",
  install: "Install",
  resume: "Resume",
  update: "How to update",
};

/** The one action a row offers as a button; Change account is never the urgent one. */
export function primaryAction(connection: Connection): ConnectionAction | null {
  const order = ["reconnect", "sign_in", "resume", "install", "update", "change_account"];
  return order.find((action) => connection.actions.includes(action)) ?? null;
}

export const isUrgent = (action: ConnectionAction | null) => action === "sign_in" || action === "reconnect";

export const CODEX_METHODS = [
  { method: "chatgpt", title: (tag: string) => `ChatGPT account for ${tag} only`,
    detail: "Recommended. Doesn't change Codex on this Mac." },
  { method: "codex", title: () => "Codex sign-in on this Mac", detail: "Shared with Codex and your other Tags." },
] as const;

export const CLAUDE_SHARED_NOTE =
  "Claude's sign-in is shared with Claude Code on this Mac. Signing in with another account changes it there too.";

/** Whether changing this account means stopping a running Tag first. */
export function needsRestart(connection: Connection, method?: string) {
  return connection.backend === "codex" && (method === "chatgpt" || connection.method === "chatgpt");
}

export const choiceLabel = (choice: Pick<ModelChoice, "backend_name" | "label">) => `${choice.backend_name} · ${choice.label}`;

export function findModel(groups: ModelGroup[], value: string) {
  for (const group of groups) {
    const entry = group.models.find((m) => m.value === value);
    if (entry) return { group, entry };
  }
  return null;
}

/** The model to show selected: the saved one while it's offered, else Tag's suggestion. */
export function selectedModel(models: AIModels) {
  return models.default.available ? models.default.value : models.suggested;
}

// ---- Commands ----------------------------------------------------------------

/** `tag [TAG] settings ai …`; the main Tag can be named too. */
export const aiArgs = (tag: string, ...rest: string[]) => [tag, "settings", "ai", ...rest];

export function signInArgs(tag: string, backend: string, options: { method?: string; restart?: boolean; resume?: boolean } = {}) {
  return [
    ...aiArgs(tag, ...(options.resume ? ["resume"] : ["sign-in", backend])),
    ...(options.method && !options.resume ? ["--method", options.method] : []),
    ...(options.restart ? ["--restart"] : []),
  ];
}

// ---- One backend's sign-in, as plain state ---------------------------------------

export interface SignInState {
  backend: string | null;
  step: SignInStep | null;
  text: string;
  url: string | null;
  result: SignInResult | null;
}

export const idleSignIn: SignInState = { backend: null, step: null, text: "", url: null, result: null };

export type SignInAction =
  | { type: "start"; backend: string }
  | { type: "line"; line: string }
  | { type: "exit"; code: number; stderr: string }
  | { type: "reset" };

export function signInReducer(state: SignInState, action: SignInAction): SignInState {
  switch (action.type) {
    case "start":
      return { ...idleSignIn, backend: action.backend, step: "browser", text: "Opening your browser…" };
    case "line": {
      const event = parseSignInLine(action.line);
      if (!event || !state.backend) return state;
      if (event.type === "progress") return { ...state, step: event.step, text: event.text, url: event.url ?? state.url };
      if (event.type === "error") {
        return { ...state, step: null, result: { type: "sign_in", backend: state.backend, status: "failed", error: event.error, retry: true } };
      }
      return { ...state, step: null, result: event };
    }
    case "exit":
      if (!state.backend || state.result) return state;
      return {
        ...state, step: null,
        result: {
          type: "sign_in", backend: state.backend, status: "failed", retry: true,
          error: action.stderr.trim().split("\n").pop()?.replace(/^Error: /, "") || "Sign-in stopped unexpectedly.",
        },
      };
    case "reset":
      return idleSignIn;
  }
}

/** What a finished sign-in says, in a sentence. */
export function resultLine(result: SignInResult, name: string) {
  if (result.status === "connected") return `${name} connected.`;
  if (result.status === "cancelled") return "Sign-in cancelled.";
  return `Sign-in didn't finish. ${result.error ?? ""}`.trim();
}
