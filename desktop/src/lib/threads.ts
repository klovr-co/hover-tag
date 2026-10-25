// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Threads: each Slack thread a Tag works in, as one conversation.
// Reads `tag NAME threads --json` and `tag NAME thread CHANNEL TS --json`; replies and
// answers go back through `thread … reply` and `thread … answer`, which the running Tag picks up.
import { useCallback, useEffect, useRef, useState } from "react";
import type { Bridge } from "./bridge";
import { liveRows } from "./home";
import { parseJSON, title, type TagRow } from "./protocol";

export const THREADS_CAPABILITY = "threads-json";
export const REPLY_CAPABILITY = "thread-reply";
export const PROMPTS_CAPABILITY = "thread-prompts";
/** How often the list is read again while it shows, and in the background for notifications. */
export const LIST_SECONDS = 10;
export const BACKGROUND_SECONDS = 30;
/** An open conversation is read this often while the Tag works or waits, otherwise less. */
export const LIVE_SECONDS = 3;
export const QUIET_SECONDS = 15;
/** A finished thread folds into Settled once it has been quiet this long. */
export const SETTLE_HOURS = 24;

export type ThreadStatus = "needs_you" | "working" | "failed" | "stopped" | "done";

export interface Person { id: string | null; name: string | null; bot?: boolean; self?: boolean }

export interface ThreadRow {
  channel: string;
  thread_ts: string;
  team: string | null;
  source: string;
  title: string | null;
  status: ThreadStatus | string;
  preview_only: boolean;
  started_at: string | null;
  updated_at: string | null;
  people: string[];
  message_count: number;
  file_count: number;
  memory_count: number;
  pending_prompts: number;
  last_answer_preview: string | null;
  channel_name: string | null;
  dm: boolean;
  link: string;
}

/** A thread in the list across all Tags: which Tag it belongs to. */
export interface ListedThread extends ThreadRow { tag: string }

export interface ThreadFile {
  name: string; kind: string; delivery: string; url?: string; local_path?: string; run_id: string | null; at: string | null;
}
export interface MemoryChange {
  action: "saved" | "changed" | "forgot" | string; scope: string | null; key: string; version: string | null;
  summary: string | null; run_id: string | null; at: string | null;
}
export interface Step { id: string; label: string; status: string }
export interface Choice { id: string; label: string; primary?: boolean; style?: "primary" | "danger" }

export type ThreadItem =
  | { type: "message"; at: string | null; ts: string | null; author: Person | null; text: string; via: string; posted_to_slack?: boolean }
  | { type: "work"; run_id: string; status: string; started_at: string | null; finished_at: string | null; steps: Step[];
      step_count: number; duration_seconds?: number; via: string; requester?: Person; preview_only?: boolean }
  | { type: "answer"; at: string | null; run_id: string | null; text: string; outcome: string; posted_to: string; ts?: string;
      files: ThreadFile[]; memory: MemoryChange[]; preview_only?: boolean }
  | Prompt
  | { type: "notice"; at: string | null; text: string; prompt_id?: string };

export interface Prompt {
  type: "prompt"; at: string | null; prompt_id: string; kind: string; text: string; choices: Choice[];
  requester: Person | null; status: "pending" | "answered" | "expired" | string; url?: string; expires_at?: string; run_id?: string;
  answer: { choice: string | null; label: string | null; by: Person | null; via: string; at: string | null } | null;
}

export interface ThreadDetail extends ThreadRow {
  items: ThreadItem[]; files: ThreadFile[]; memory: MemoryChange[]; prompts: Prompt[];
}

/** One Tag's list; ignores rows it can't use. */
export function parseThreads(stdout: string): { retention_days: number; has_more: boolean; threads: ThreadRow[] } {
  const data = parseJSON<{ retention_days?: number; has_more?: boolean; threads?: ThreadRow[] }>(stdout);
  return {
    retention_days: typeof data.retention_days === "number" ? data.retention_days : 90,
    has_more: data.has_more === true,
    threads: Array.isArray(data.threads) ? data.threads.filter((row) => typeof row?.channel === "string" && typeof row.thread_ts === "string") : [],
  };
}

export type ThreadRead = { thread: ThreadDetail } | { missing: string };

/** One conversation, or the CLI's reason it isn't on this computer. */
export function parseThread(code: number, stdout: string): ThreadRead {
  const data = parseJSON<{ ok?: boolean; thread?: ThreadDetail; code?: string; error?: string }>(stdout);
  if (code === 0 && data.thread) return { thread: { ...data.thread, items: data.thread.items ?? [], files: data.thread.files ?? [],
    memory: data.thread.memory ?? [], prompts: data.thread.prompts ?? [] } };
  if (data.code === "not_found") return { missing: data.error ?? NOT_HERE };
  throw new Error(data.error ?? "Couldn't read this thread.");
}

/** docs/troubleshooting.md: a hover-tag:// link for a Tag or thread that isn't on this computer. */
export const NOT_HERE = "This thread isn't on this computer. Open the link on the computer that runs the Tag.";

export const threadKey = (thread: { tag: string; channel: string; thread_ts: string }) => `${thread.tag}/${thread.channel}/${thread.thread_ts}`;

const time = (iso: string | null | undefined) => (iso ? new Date(iso).getTime() : 0) || 0;

/** "Needs you" first, then the newest; done threads that went quiet fold into Settled. */
export function arrange(rows: ListedThread[], now = new Date()): { active: ListedThread[]; settled: ListedThread[] } {
  const newest = (a: ListedThread, b: ListedThread) => time(b.updated_at) - time(a.updated_at);
  const quiet = (row: ListedThread) => row.status === "done" && now.getTime() - time(row.updated_at) > SETTLE_HOURS * 3600_000;
  const needs = rows.filter((row) => row.status === "needs_you").sort(newest);
  const others = rows.filter((row) => row.status !== "needs_you");
  return { active: [...needs, ...others.filter((row) => !quiet(row)).sort(newest)], settled: others.filter(quiet).sort(newest) };
}

/** "now", "5m", "2h", "3d", or a date. */
export function ago(iso: string | null | undefined, now = new Date()) {
  const at = time(iso);
  if (!at) return "";
  const minutes = Math.max(0, Math.round((now.getTime() - at) / 60_000));
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m`;
  if (minutes < 24 * 60) return `${Math.round(minutes / 60)}h`;
  if (minutes < 7 * 24 * 60) return `${Math.round(minutes / 1440)}d`;
  return new Date(at).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

export const STATUS_TEXT: Record<string, string> = {
  needs_you: "Needs you", working: "Working", failed: "Couldn't finish", stopped: "Stopped", done: "Done",
};

/** Where a thread is: "#launch", "DM", or the channel ID while Tag hasn't recorded a name. */
export const placeOf = (row: ThreadRow) => row.dm ? "DM" : `#${row.channel_name ?? row.channel}`;

/** A row's name: its first question, else the latest answer. */
export const threadTitle = (row: ThreadRow) => row.title?.trim() || row.last_answer_preview?.trim() || "Thread";

export interface Threads {
  rows: ListedThread[];
  /** Every Tag's list has been read once. */
  loaded: boolean;
  /** Tags whose list couldn't be read. */
  failed: string[];
  refresh: () => void;
}

/**
 * Every Tag's threads in one list. Running Tags are read every `seconds`; a stopped Tag's
 * threads don't change, so it is read once. A thread that newly needs the owner shows a notification.
 */
export function useThreads(api: Bridge | null, tagRows: TagRow[], { enabled, seconds, notify }: {
  enabled: boolean; seconds: number; notify?: (row: ListedThread, tag: TagRow) => void;
}): Threads {
  const [rows, setRows] = useState<ListedThread[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState<string[]>([]);
  const [round, setRound] = useState(0);
  const live = liveRows(tagRows).filter((row) => row.valid);
  const ids = live.map((row) => `${row.id}:${row.state === "running" ? 1 : 0}`).join(",");
  const byTag = useRef(new Map<string, { running: boolean; rows: ListedThread[] }>());
  const waiting = useRef<Set<string> | null>(null);
  const tagsRef = useRef(live);
  tagsRef.current = live;
  const notifyRef = useRef(notify);
  notifyRef.current = notify;

  useEffect(() => {
    if (!api || !enabled) return;
    let current = true;
    const forced = round;
    const read = async (force: boolean) => {
      const tags = ids ? ids.split(",").map((entry) => { const [id, on] = entry.split(":"); return { id, running: on === "1" }; }) : [];
      const failures: string[] = [];
      // One at a time: each read starts the tag command.
      for (const { id, running } of tags) {
        const known = byTag.current.get(id);
        if (!force && known && !running && !known.running) continue;
        try {
          const result = await api.tag([id, "threads", "--json"]);
          if (result.code !== 0) throw new Error(result.stderr);
          byTag.current.set(id, { running, rows: parseThreads(result.stdout).threads.map((row) => ({ ...row, tag: id })) });
        } catch {
          failures.push(id);
        }
        if (!current) return;
      }
      for (const id of [...byTag.current.keys()]) if (!tags.some((tag) => tag.id === id)) byTag.current.delete(id);
      const all = [...byTag.current.values()].flatMap((entry) => entry.rows);
      // Notify for threads that started waiting since the last read, never for those already waiting at launch.
      const now = new Set(all.filter((row) => row.status === "needs_you").map((row) => `${threadKey(row)}@${row.updated_at}`));
      if (waiting.current) {
        for (const row of all) {
          const key = `${threadKey(row)}@${row.updated_at}`;
          const tag = tagsRef.current.find((t) => t.id === row.tag);
          if (row.status === "needs_you" && !waiting.current.has(key) && tag) notifyRef.current?.(row, tag);
        }
      }
      waiting.current = now;
      setRows(all);
      setFailed(failures);
      setLoaded(true);
    };
    void read(forced > 0);
    const timer = setInterval(() => void read(false), seconds * 1000);
    return () => { current = false; clearInterval(timer); };
  }, [api, enabled, ids, seconds, round]);

  const refresh = useCallback(() => setRound((n) => n + 1), []);
  return { rows, loaded, failed, refresh };
}

/** The notification for a thread that waits on its owner. */
export function needsYouNotice(row: ThreadRow, tag: TagRow) {
  return { title: `${title(tag)} needs you`, body: `${threadTitle(row)} · ${placeOf(row)}` };
}

export interface OpenThread {
  thread: ThreadDetail | null;
  /** Set when this computer doesn't have the thread. */
  missing: string | null;
  error: string | null;
  refresh: () => Promise<void>;
}

/** One conversation, read again often while the Tag works or waits for an answer. */
export function useThread(api: Bridge, target: { tag: string; channel: string; thread_ts: string } | null): OpenThread {
  const [thread, setThread] = useState<ThreadDetail | null>(null);
  const [missing, setMissing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = target ? threadKey(target) : "";
  const sequence = useRef(0);
  const read = useCallback(async () => {
    if (!key) return;
    const [tag, channel, ts] = key.split("/");
    const mine = ++sequence.current;
    try {
      const result = await api.tag([tag, "thread", channel, ts, "--json"]);
      const read = parseThread(result.code, result.stdout);
      if (mine !== sequence.current) return;
      if ("thread" in read) { setThread(read.thread); setMissing(null); } else { setThread(null); setMissing(read.missing); }
      setError(null);
    } catch (problem) {
      if (mine === sequence.current) setError(problem instanceof Error ? problem.message : String(problem));
    }
  }, [api, key]);
  useEffect(() => { setThread(null); setMissing(null); setError(null); }, [key]);
  const busy = thread?.status === "working" || thread?.status === "needs_you";
  useEffect(() => {
    if (!key) return;
    void read();
    const timer = setInterval(() => void read(), (busy ? LIVE_SECONDS : QUIET_SECONDS) * 1000);
    return () => { sequence.current++; clearInterval(timer); };
  }, [key, busy, read]);
  return { thread, missing, error, refresh: read };
}

type Sent = { ok: true } | { ok: false; error: string };

const sent = (result: { code: number; stdout: string; stderr: string }): Sent => {
  if (result.code === 0) return { ok: true };
  try { return { ok: false, error: parseJSON<{ error?: string }>(result.stdout).error ?? "Couldn't send it." }; }
  catch { return { ok: false, error: result.stderr.trim() || "Couldn't send it." }; }
};

/** Send the owner's reply to the Tag; with `postToSlack`, it is also posted in the Slack thread. */
export async function sendReply(api: Bridge, target: { tag: string; channel: string; thread_ts: string }, text: string, postToSlack: boolean) {
  return sent(await api.tag([target.tag, "thread", target.channel, target.thread_ts, "reply", `--text=${text}`,
    ...(postToSlack ? ["--post-to-slack"] : []), "--json"]));
}

/** Answer a question the Tag is waiting on; Slack's copy of it closes too. */
export async function answerPrompt(api: Bridge, target: { tag: string; channel: string; thread_ts: string }, prompt: string, choice: string) {
  return sent(await api.tag([target.tag, "thread", target.channel, target.thread_ts, "answer", prompt, choice, "--json"]));
}
