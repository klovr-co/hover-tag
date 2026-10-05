// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// What Home says about your Tags, as plain data: the quiet line under the
// sky, each row's two lines, and the summary. Everything comes from what
// `tag list`, `tag NAME logs --json` and `tag NAME settings ai --json` report.
import type { AIStatus } from "./ai";
import { status, title, type TagRow } from "./protocol";

/** One thing a Tag did, from `tag NAME logs --json` → `activity`. */
export interface ActivityItem {
  run_id?: string;
  reply_preview?: string;
  reply_summary?: string;
  reply_summary_status?: "pending" | "ready" | "unavailable";
  backend?: string;
  model?: string;
  model_name?: string;
  reasoning_effort?: string;
  duration_seconds?: number;
  /** Tool steps recorded for the run, including ones omitted from the saved timeline. */
  step_count?: number;
  usage?: { input_tokens: number; output_tokens: number; total_tokens: number;
    cache_read_input_tokens?: number; cache_creation_input_tokens?: number; reasoning_output_tokens?: number };
  artifacts?: { name: string; kind: "file" | "image"; delivery: "uploaded" | "local" | "upload_failed";
    url?: string; local_path?: string }[];
  artifact_thread_url?: string;

  at: string;
  kind: "replied" | "failed" | "stopped" | "working" | string;
  channel: string;
  channel_name: string | null;
  dm: boolean;
}

export interface ActivityDetail {
  run_id: string;
  outcome: string;
  started_at: string;
  finished_at: string | null;
  team: string;
  channel: string;
  thread_ts: string;
  events: { label: string; status: string; started_at: string; finished_at: string | null;
    details: { tool?: string; input?: string; output?: string } }[];
  omitted: number;
  error: { reference: string; text: string } | null;
}

export const EFFORT_LABEL: Record<string, string> = {
  minimal: "Minimal", low: "Low", medium: "Medium", high: "High", xhigh: "Extra high", max: "Max", ultra: "Ultra",
};
/** Home keeps the thinking level short and lowercase. */
const EFFORT_SHORT: Record<string, string> = { medium: "med" };
export const effortLabel = (effort: string) => EFFORT_LABEL[effort] ?? effort;
export const effortShort = (effort: string) => EFFORT_SHORT[effort] ?? effort.toLowerCase();

/** Tags past setup: the ones that can run and answer. */
export const liveRows = (rows: TagRow[]) => rows.filter((row) => status(row) !== "setup");

/** Why a Tag's AI can't answer right now, leading with the cause; null when it can. */
export function aiProblem(report: AIStatus | undefined | null): string | null {
  if (!report) return null;
  const choice = report.default_model;
  const connection = report.connections.find((c) => c.backend === choice.backend);
  if (!connection) return null;
  const name = connection.name;
  switch (connection.state) {
    case "expired": return `${name} sign-in expired`;
    case "signed_out": return `${name} isn't signed in`;
    case "not_installed": return `${name} isn't installed`;
    case "unsupported": return `${name} needs an update`;
  }
  if (connection.state === "connected" && choice.available === false) return `${choice.label} was retired`;
  return null;
}

export type QuietLine =
  | { kind: "ai"; cause: string | null; who: string; causes: string[]; tag: string }
  | { kind: "reply"; tag: string; name: string; avatar: TagRow; today: boolean; place: string; when: string }
  | { kind: "greeting"; hello: string; name: string | null; listening: boolean };

const sameDay = (a: Date, b: Date) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
const clock = (d: Date) => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;

/** "10:12" today, "Yesterday 17:02", "Mon 09:10" this week, then "2 Oct". */
export function whenText(at: Date, now: Date) {
  if (sameDay(at, now)) return clock(at);
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(at, yesterday)) return `Yesterday ${clock(at)}`;
  if (now.getTime() - at.getTime() < 6 * 86400_000) {
    return `${at.toLocaleDateString("en-GB", { weekday: "short" })} ${clock(at)}`;
  }
  return at.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

export const placeText = (item: ActivityItem) => item.dm ? "a direct message" : item.channel_name ? `#${item.channel_name}` : "a channel";

/** "Maya's Tag", "Maya's Tag and Research Tag", or "3 Tags". */
export function whoText(names: string[]) {
  return names.length === 1 ? names[0] : names.length === 2 ? `${names[0]} and ${names[1]}` : `${names.length} Tags`;
}

/**
 * The one line between the header and the cards. First match wins: an AI
 * that can't answer (grouped by cause, since one sign-in serves every Tag on
 * this computer), else the latest reply, else a greeting.
 */
export function quietLine(rows: TagRow[], problems: Record<string, string | null>,
  activity: Record<string, ActivityItem[]>, now: Date, firstName: string | null): QuietLine {
  const live = liveRows(rows);
  const broken = live.filter((row) => problems[row.id]);
  if (broken.length) {
    const causes = [...new Set(broken.map((row) => problems[row.id]!))];
    return { kind: "ai", cause: causes.length === 1 ? causes[0] : null, causes, who: whoText(broken.map(title)), tag: broken[0].id };
  }
  const replies = live.flatMap((row) => (activity[row.id] ?? [])
    .filter((item) => item.kind === "replied" && !Number.isNaN(Date.parse(item.at)))
    .map((item) => ({ row, item, at: new Date(item.at) })));
  replies.sort((a, b) => b.at.getTime() - a.at.getTime());
  const latest = replies[0];
  if (latest) {
    return {
      kind: "reply", tag: latest.row.id, name: title(latest.row), avatar: latest.row, today: sameDay(latest.at, now),
      place: placeText(latest.item), when: whenText(latest.at, now),
    };
  }
  const hour = now.getHours();
  const hello = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  return { kind: "greeting", hello, name: firstName, listening: live.some((row) => row.state === "running") };
}

export type RowLine =
  | { kind: "error"; text: string }
  | { kind: "ai"; text: string }
  | { kind: "description"; text: string }
  | { kind: "none" };

/** A row's second line: the description, unless something needs you. */
export function rowLine(row: TagRow, problem: string | null, error: string): RowLine {
  if (status(row) === "attention") return { kind: "error", text: error };
  if (problem) return { kind: "ai", text: `Can't answer · ${problem}` };
  return row.description ? { kind: "description", text: row.description } : { kind: "none" };
}

/** The model after the name, such as "GPT-5.5 · med". */
export function modelText(row: TagRow, report?: AIStatus | null) {
  let name = row.default_model_name ?? report?.default_model.label ?? null;
  // Older installed CLIs omit the backend from this display name.
  if (name === "Account default") {
    const backend = row.default_model?.split(":")[0] ?? report?.default_model.backend;
    if (backend === "codex" || backend === "claude") {
      name = `${backend === "codex" ? "Codex" : "Claude"} default`;
    }
  }
  if (!name) return null;
  const effort = row.default_effort;
  return { text: effort ? `${name} · ${effortShort(effort)}` : name, model: name, effort: effort ?? null };
}

/** "2 of 3 online · 1 to finish", under "Your Tags". */
export function summary(rows: TagRow[]) {
  const live = liveRows(rows);
  const setup = rows.length - live.length;
  const online = live.filter((row) => row.state === "running").length;
  if (!rows.length) return { text: "No Tags yet", on: false };
  if (!live.length) return { text: `${setup} Tag${setup === 1 ? "" : "s"} to finish setting up`, on: false };
  return { text: `${online} of ${live.length} online${setup ? ` · ${setup} to finish` : ""}`, on: online > 0 };
}

/** Up to four online Tags for the header; empty when none are online. */
export function roster(rows: TagRow[]) {
  const online = liveRows(rows).filter((row) => row.state === "running");
  const shown = online.slice(0, 4);
  return { shown, extra: online.length - shown.length };
}

/** Elapsed backend time includes tool work, excludes the separate summary job. */
export function generationTime(seconds: number): string {
  const rounded = Math.max(0, Math.round(seconds));
  return rounded < 60 ? `${rounded}s` : `${Math.floor(rounded / 60)}m ${rounded % 60}s`;
}
