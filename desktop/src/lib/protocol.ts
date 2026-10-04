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

export interface SlackPerson {
  id: string;
  name: string;
  username: string;
  image_url?: string | null;
}

export interface SetupQuestion {
  type: "question";
  id: string;
  kind: "choose" | "multi" | "text" | "secret" | "confirm" | "people" | "slack_login" | string;
  prompt: string;
  options?: string[];
  default?: number | string | boolean | null;
  selected?: number[];
  sign_in_line?: string;
  people?: SlackPerson[];
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
}

export type SetupEvent =
  | { type: "message"; text: string }
  | SetupQuestion
  | { type: "result"; status: "complete" | "paused" | "failed" | string; tag?: string; error?: string }
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

export function personMatches(person: SlackPerson, query: string) {
  const q = query.trim().toLocaleLowerCase();
  return !q || [person.name, person.username, person.id].some((v) => v.toLocaleLowerCase().includes(q));
}

// ---- Installer progress ------------------------------------------------------

export const INSTALL_STEPS = [
  { step: "tools", title: "Getting installation tools", detail: "A small, verified download", weight: 0.1 },
  { step: "python", title: "Preparing Python", detail: "A private copy, so your system stays untouched", weight: 0.15 },
  { step: "download", title: "Downloading Tag", detail: "The latest release, checked before it's used", weight: 0.1 },
  { step: "components", title: "Installing components", detail: "Slack connection and local search", weight: 0.3 },
  { step: "memory", title: "Preparing local memory", detail: "Downloads a search model — the longest step", weight: 0.3 },
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
