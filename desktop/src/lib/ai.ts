// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// The app's side of "AI connections" in docs/reference/app-protocol.md: which
// agents a Tag can use, its default model, and browser sign-in. Tag does the
// checks and sign-ins; this file only reads what `tag … settings ai` prints.
import { parseJSON } from "./protocol";

export const AI_CAPABILITY = "ai-connections";
export const SHARED_AI_CAPABILITY = "shared-ai-connections";
export const API_CAPABILITY = "api-connections";

export type ConnectionState =
  | "connected" | "signed_out" | "expired" | "limited" | "not_installed" | "unsupported" | "misconfigured" | string;
export type ConnectionAction = "sign_in" | "reconnect" | "change_account" | "install" | "resume" | "update" | string;

export interface Connection {
  backend: "codex" | "claude" | string;
  name: string;
  provider: string;
  state: ConnectionState;
  installed: boolean;
  version?: string | null;
  /** codex: the computer's Codex sign-in; chatgpt: a ChatGPT plan shared by all Tags; api: this Tag's own API. */
  method?: "codex" | "chatgpt" | "claude" | "api" | string | null;
  account?: string | null;
  detail?: string;
  /** True when the sign-in is shared by all Tags. */
  shared: boolean;
  actions: ConnectionAction[];
  install_url: string;
  allowed?: boolean;
  api?: ApiSummary;
}

export type ApiKind = "openai" | "anthropic" | "azure";

/** A Tag's own API connection, as `tag … settings ai api` reports it. It never includes the key. */
export interface ApiSummary {
  backend: "codex" | "claude" | string;
  kind: ApiKind | string;
  kind_name: string;
  base_url: string;
  host: string;
  models: string[];
  api_version: string;
  key_set: boolean;
  /** Why the Tag can't use it yet, or "". */
  problem: string;
}

/** One Tag using its own API, from `tag settings ai connections`. */
export interface ApiConnection extends ApiSummary {
  tag: string;
  tag_name: string;
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

export interface AIConnections {
  connections: Connection[];
  usable: string[];
  running: boolean;
  scope: "installation";
  /** Tags using their own API instead of the shared sign-in; absent before api-connections. */
  api_connections?: ApiConnection[];
}

export const parseConnections = (output: string) => parseJSON<AIConnections>(output);

export interface AIStatus {
  tag: string;
  running: boolean;
  connections: Connection[];
  usable: string[];
  default_model: ModelChoice;
  /** The thinking level the default model uses: the saved one, or the model's own default. */
  default_effort?: string | null;
  /** The default model's thinking levels; empty when it has none or Tag doesn't know them yet. */
  effort_levels?: string[];
  /** Whether a thinking level was chosen for this Tag, rather than the model's default. */
  effort_chosen?: boolean;
  checked_at?: string;
}

export interface ModelEntry {
  value: string;
  model: string | null;
  label: string;
  default: boolean;
  /** The model's thinking levels, and its own default level; empty and null when it has none. */
  efforts?: string[];
  default_effort?: string | null;
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
  misconfigured: "API needs attention",
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
  { method: "chatgpt", title: () => "ChatGPT account for all Tags",
    detail: "Shared by all Tags. Doesn't change Codex on this Mac." },
  { method: "codex", title: () => "Codex sign-in on this Mac", detail: "Shared with Codex and your other Tags." },
] as const;

export const CLAUDE_SHARED_NOTE =
  "Claude's sign-in is shared with Claude Code on this Mac. Signing in with another account changes it there too.";



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

export function signInArgs(backend: string, options: { method?: string; restart?: boolean; resume?: boolean } = {}) {
  return [
    "settings", "ai", ...(options.resume ? ["resume"] : ["sign-in", backend]),
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
  if (result.status === "connected") return `${name} connected.${result.error ? ` ${result.error}` : ""}`;
  if (result.status === "cancelled") return "Sign-in cancelled.";
  return `Sign-in didn't finish. ${result.error ?? ""}`.trim();
}

// ---- A Tag's own API ---------------------------------------------------------------

/** Tag's two agents, and the API each one speaks. Azure OpenAI is Codex on an Azure endpoint, found from its URL. */
export const API_AGENTS = {
  codex: { name: "Codex", speaks: "OpenAI-compatible APIs, including Azure OpenAI", url: "https://api.openai.com/v1", model: "gpt-5.5" },
  claude: { name: "Claude", speaks: "Anthropic-compatible APIs", url: "https://api.anthropic.com", model: "claude-sonnet-5-5" },
} as const;

/** Azure OpenAI resource endpoints, which take a resource key, deployment names, and an API version. */
export const isAzureUrl = (url: string) => /^https:\/\/[^/]+\.(openai\.azure\.com|cognitiveservices\.azure\.com)(\/|$)/i.test(url.trim());

/** The provider kind Tag expects for this form. */
export const apiKind = (form: Pick<ApiForm, "backend" | "baseUrl">): ApiKind =>
  form.backend === "claude" ? "anthropic" : isAzureUrl(form.baseUrl) ? "azure" : "openai";

export const hostOf = (url: string) => { try { return new URL(url).host; } catch { return url; } };

/** `Codex · API (gateway.example.com)`, the same words as the CLI. */
export const apiLabel = (api: Pick<ApiSummary, "backend" | "kind" | "host">) =>
  `${api.backend === "codex" ? "Codex" : "Claude"} · ${api.kind === "azure" ? "Azure" : "API"} (${api.host})`;

export interface ApiForm {
  backend: "codex" | "claude";
  baseUrl: string;
  models: string;
  apiVersion: string;
}

/** Splits "a, b,,a" into ["a", "b"], as Tag does. */
export const modelList = (text: string) => [...new Set(text.split(",").map((m) => m.trim()).filter(Boolean))];

/** What the form still needs before it can be sent; Tag checks the rest. */
export function apiFormProblem(form: ApiForm, key: string, keySaved: boolean, tags: number): string | null {
  if (!tags) return "Add a Tag first.";
  const url = form.baseUrl.trim();
  if (url && !/^https:\/\//i.test(url) && !/^http:\/\/(localhost|127\.0\.0\.1|\[::1\])(:|\/|$)/i.test(url)) {
    return "Use an https:// address.";
  }
  if (!modelList(form.models).length) return apiKind(form) === "azure" ? "Enter at least one deployment name." : "Enter at least one model.";
  if (!key.trim() && !keySaved) return "Paste the API key.";
  return null;
}

/** `tag TAG settings ai api set …`; the key goes over stdin, never here. */
export function apiSetArgs(tag: string, form: ApiForm) {
  const kind = apiKind(form);
  return [tag, "settings", "ai", "api", "set", "--backend", form.backend, "--kind", kind,
    "--base-url", form.baseUrl.trim(), "--models", modelList(form.models).join(","),
    ...(kind === "azure" && form.apiVersion.trim() ? ["--api-version", form.apiVersion.trim()] : []),
    "--restart"];
}

export const apiClearArgs = (tag: string, backend: string) =>
  [tag, "settings", "ai", "api", "clear", "--backend", backend, "--restart"];
export const apiCheckArgs = (tag: string, backend: string) =>
  [tag, "settings", "ai", "api", "check", "--backend", backend, "--json"];

export interface ApiResult {
  type: "api";
  action: "set" | "clear" | string;
  backend: string;
  status: "saved" | "failed" | string;
  api?: ApiSummary | null;
  error?: string;
  restarted?: boolean;
  /** False when the Tag's default model runs on the other agent, so it doesn't use this API yet. */
  in_use?: boolean;
}

export interface ApiCheck {
  ok: boolean;
  checks: { name: string; ok: boolean; text: string }[];
}

/** One stdout line from `api set|clear`: a progress text, the result, or nothing. */
export function parseApiLine(line: string): { text: string } | ApiResult | null {
  const text = line.trim();
  if (!text.startsWith("{")) return null;
  try {
    const event = JSON.parse(text);
    if (event?.type === "progress" && typeof event.text === "string") return { text: event.text };
    if (event?.type === "api") return event as ApiResult;
    if (event?.ok === false && typeof event.error === "string") return { type: "api", action: "", backend: "", status: "failed", error: event.error };
  } catch {
    // Not one of ours.
  }
  return null;
}

/** One API shared by several Tags: the same agent, endpoint and models. */
export interface ApiGroup extends ApiSummary {
  tags: { id: string; name: string }[];
}

/** Tags with the same API become one row; a Tag whose API differs gets its own. */
export function groupApis(items: ApiConnection[]): ApiGroup[] {
  const groups = new Map<string, ApiGroup>();
  for (const item of items) {
    const key = [item.backend, item.kind, item.base_url, item.api_version, item.models.join(","), item.problem].join("|");
    const { tag, tag_name, ...summary } = item;
    const group = groups.get(key) ?? { ...summary, tags: [] };
    group.tags.push({ id: tag, name: tag_name });
    groups.set(key, group);
  }
  return [...groups.values()];
}
