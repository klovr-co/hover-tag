// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// The app's side of docs/reference/app-protocol.md. Tag does the work; this
// file only reads what `tag … --json` prints. Tests parse the shared examples
// in protocol/examples, the same files the CLI tests check.

/** Newest incompatible protocol this app understands. */
export const APP_PROTOCOL = 1;

export interface VersionInfo {
  version: string;
  app_protocol: number;
  capabilities: string[];
  platform: string;
}

export interface TagRow {
  id: string;
  valid: boolean;
  state?: string | null;
  slack_workspace?: string | null;
  workspace_name?: string | null;
  /** Local copy of the Slack workspace's icon; null for Slack's default icon. */
  workspace_icon?: string | null;
  slack_name?: string | null;
  nickname?: string | null;
  avatar?: string | null;
  keep_running?: boolean;
  main?: boolean;
  error?: string;
  /** The one-line description people gave the Tag (its Slack app description). */
  description?: string | null;
  /** The Tag's default model, such as "codex:gpt-5.5", and how people read it. */
  default_model?: string | null;
  default_model_label?: string | null;
  /** Just the model, such as "GPT-5.5". */
  default_model_name?: string | null;
  /** The thinking level the default model uses; null for models without levels. */
  default_effort?: string | null;
  /** The channels the Tag answers in; `name` is null until Tag has recorded it. */
  channels?: { id: string; name: string | null }[];
}

export type Status = "online" | "offline" | "setup" | "attention";

export function status(row: TagRow): Status {
  switch (row.state) {
    case "running":
      return "online";
    case "stopped":
    case "configured":
      return "offline";
    case "not_configured":
    case "setup_incomplete":
      return "setup";
    default:
      return "attention";
  }
}

export const STATUS_LABEL: Record<Status, string> = {
  online: "Online",
  offline: "Offline",
  setup: "Needs setup",
  attention: "Needs attention",
};

/** What went wrong with a Tag that needs attention, in its own words when Tag gave one. */
export function problemText(row: TagRow) {
  if (row.error) return row.error;
  return row.state === "invalid_configuration" ? "Its settings can't be read"
    : row.state === "invalid_tag" ? "This Tag's folder is damaged" : "Needs attention";
}

/** People see the Tag's Slack name and workspace; the ID is only for commands. */
export const title = (row: TagRow) => row.slack_name || "New Tag";

export interface Group {
  key: string;
  label: string;
  icon: string | null;
  rows: TagRow[];
}

/** Tags grouped by Slack workspace, in list order. Several Tags can share one. */
export function groups(rows: TagRow[]): Group[] {
  const byKey = new Map<string, TagRow[]>();
  for (const row of rows) {
    const key = row.slack_workspace ?? "";
    byKey.set(key, [...(byKey.get(key) ?? []), row]);
  }
  return [...byKey].map(([key, members]) => ({
    key,
    label: members[0].workspace_name || key || "Not connected yet",
    icon: members.find((row) => row.workspace_icon)?.workspace_icon ?? null,
    rows: members,
  }));
}

/** Commands print JSON, sometimes after warnings; read from the first brace. */
export function parseJSON<T>(output: string): T {
  const start = output.indexOf("{");
  if (start < 0) throw new Error("Tag printed no JSON");
  return JSON.parse(output.slice(start)) as T;
}

export function parseList(output: string): TagRow[] {
  const listing = parseJSON<{ tags?: TagRow[] }>(output);
  if (!Array.isArray(listing.tags)) throw new Error("Tag list has no tags");
  return listing.tags;
}

/** Whether this app can drive the installed Tag, and what it's missing. */
export function compatibility(info: VersionInfo, needed: string[]) {
  if (info.app_protocol > APP_PROTOCOL) return { ok: false, reason: "app-too-old" as const, missing: [] };
  const missing = needed.filter((c) => !info.capabilities.includes(c));
  return { ok: missing.length === 0, reason: missing.length ? ("tag-too-old" as const) : null, missing };
}

// ---- Setup over JSON lines ---------------------------------------------------

export interface SetupQuestion {
  type: "question";
  id: string;
  kind: "choose" | "multi" | "text" | "secret" | "confirm" | "profile_picture" | "slack_login" | string;
  prompt: string;
  options?: string[];
  default?: number | string | boolean | null;
  selected?: number[];
  sign_in_line?: string;
  can_go_back?: boolean;
  /** Stable answers for `choose` options, such as "sign_in:claude" (ai_connection) or model values. */
  option_ids?: string[];
  /** ai_connection: each agent's connection, as `tag settings ai --json` reports it. */
  connections?: import("./ai").Connection[];
  can_continue?: boolean;
  tag_name?: string;
  last_result?: import("./ai").SignInResult;
  /** default_model: the connected accounts' models, grouped by agent. */
  groups?: import("./ai").ModelGroup[];
  // ---- Onboarding v2 (capability setup-v2) ----
  /** profile: the Tag's name, description and the picture Tag will upload. */
  name?: string;
  name_limit?: number;
  description?: string;
  description_limit?: number;
  preview?: string | null;
  picture?: "waterdrop" | "custom" | string;
  picture_label?: string;
  error?: string | null;
  editing?: boolean;
  can_use_existing?: boolean;
  /** workspace / org_workspace: the Slack CLI's sign-ins, or an organization's workspaces. */
  workspaces?: SetupWorkspace[];
  organization?: { id: string; name: string } | null;
  /** approve_setup: everything Create will use. */
  recap?: SetupRecap;
  /** existing_app: Slack apps Tag knows; app_checks: what the chosen app is missing. */
  apps?: SetupApp[];
  checks?: { label: string; ok: boolean; detail: string | null }[];
  /** channels: every channel Slack lists to the new app; [] is allowed. */
  channels?: SetupChannel[];
  allow_empty?: boolean;
}

export interface SetupWorkspace {
  id: string;
  name: string;
  kind?: "workspace" | "organization" | string;
  user_id?: string | null;
  user_name?: string | null;
}

export interface SetupRecap {
  name: string;
  description: string;
  picture: string | null;
  workspace: { id: string; name: string; organization: { id: string; name: string } | null };
  owner: { id: string; name: string | null };
  ai: { value: string; backend: string; backend_name: string; label: string } | null;
  approval: boolean;
}

export interface SetupApp {
  id: string;
  name: string;
  source: "linked" | "cli" | "tag" | string;
  used_by: string | null;
}

export interface SetupChannel {
  id: string;
  name: string;
  member: boolean;
  private: boolean;
  members?: number | null;
}

/** What the Ready screen needs once setup completes. */
export interface SetupReady {
  team: string;
  app_id: string;
  channels: { id: string; name: string }[];
  ai: { backend: string; backend_name: string; label: string } | null;
}

export type SetupEvent =
  | { type: "message"; text: string }
  | SetupQuestion
  | { type: "result"; status: "complete" | "paused" | "failed" | string; tag?: string; error?: string; ready?: SetupReady }
  /** A step of creating the Slack app, from Create in Slack. */
  | { type: "progress"; step: string; text: string; backend?: undefined }
  /** An agent sign-in started from the AI step; never the end of setup. */
  | import("./ai").SignInEvent;

/** One stdout line from setup; anything that isn't a JSON event is ignored. */
export function parseSetupLine(line: string): SetupEvent | null {
  const text = line.trim();
  if (!text.startsWith("{")) return null;
  try {
    const event = JSON.parse(text);
    return event && typeof event.type === "string" ? (event as SetupEvent) : null;
  } catch {
    return null;
  }
}

// ---- Installer progress ------------------------------------------------------

export const INSTALL_STEPS = [
  { step: "tools", title: "Getting installation tools", detail: "A small, verified download", weight: 0.1 },
  { step: "python", title: "Preparing Python", detail: "A private copy, so your system stays untouched", weight: 0.15 },
  { step: "download", title: "Downloading Tag", detail: "The latest release, checked before it's used", weight: 0.1 },
  { step: "components", title: "Installing components", detail: "Slack connection and local search", weight: 0.3 },
  { step: "memory", title: "Preparing local memory", detail: "Downloads a search model, the longest step", weight: 0.3 },
  { step: "command", title: "Finishing up", detail: "Adding the tag command", weight: 0.05 },
] as const;

export interface InstallProgress {
  step: string;
  version?: string;
  command?: string;
  message?: string;
}

const PREFIX = "@tag-progress ";

export function parseProgressLine(line: string): InstallProgress | null {
  const at = line.indexOf(PREFIX);
  if (at < 0) return null;
  try {
    return JSON.parse(line.slice(at + PREFIX.length));
  } catch {
    return null;
  }
}

/** Index of the installer step a progress event belongs to; "release" is part of downloading. */
export function stepIndex(step: string): number {
  const name = step === "release" ? "download" : step;
  return INSTALL_STEPS.findIndex((s) => s.step === name);
}

/** Strip colour codes and carriage-return redraws from installer output. */
export function cleanLog(chunk: string) {
  // eslint-disable-next-line no-control-regex
  return chunk.replace(/\u001b\[[0-9;?]*[A-Za-z]/g, "").replace(/\r/g, "\n");
}
