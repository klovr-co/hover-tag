// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Which animation each Tag's waterdrop plays. A Tag's row says whether it is
// on, off, setting up or broken; its activity-now.json (see `activity_now` in
// docs/reference/app-protocol.md) says whether it is working right now, and
// when a request starts or finishes the drop plays a one-off moment.
import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ActivityItem } from "./home";
import { status, type TagRow } from "./protocol";
import { PERIOD, type Mood } from "./sprite";

/** How often a running Tag's activity-now.json is read: a small local file, no command. */
export const NOW_SECONDS = 2;

export interface Now {
  running: { run_id: string; thread: string; started_at: string }[];
  last: { run_id: string; thread: string; outcome: string; at: string } | null;
}

export function parseNow(value: unknown): Now | null {
  if (!value || typeof value !== "object") return null;
  const { running, last } = value as Partial<Now>;
  if (!Array.isArray(running)) return null;
  return { running: running.filter((run) => typeof run?.run_id === "string"),
    last: last && typeof last.run_id === "string" ? last : null };
}

/** What the Tag is doing, without the one-off moments. */
export function baseMood(row: TagRow, { busy = false, problem = null, now = null, latest }: {
  busy?: boolean; problem?: string | null; now?: Now | null; latest?: ActivityItem;
} = {}): Mood {
  // Switching it on fills the drop up, as Home says "Starting…".
  if (busy && row.state !== "running") return "start";
  const state = status(row);
  if (state === "offline") return "off";
  if (state === "setup") return "setup";
  if (state === "attention" || problem) return "attn";
  // Without activity-now.json (an older Tag), the slower activity read still shows work.
  const working = now ? now.running.length > 0 : latest?.kind === "working";
  return working ? "work" : "idle";
}

/** A one-off animation for what changed between two reads, or null. */
export function moment(before: Now | null, after: Now): Mood | null {
  if (!before) return null;  // The first read is history, not news.
  const finished = after.last && after.last.run_id !== before.last?.run_id ? after.last : null;
  if (finished?.outcome === "completed") return "reply";
  if (finished?.outcome === "failed") return "attn";
  const known = new Set(before.running.map((run) => run.run_id));
  if (after.running.some((run) => !known.has(run.run_id))) return "mention";
  return null;
}

export type MoodOf = (row: TagRow) => Mood;
export const MoodContext = createContext<MoodOf>((row) => baseMood(row));
export const useMoodOf = () => useContext(MoodContext);

/**
 * Read each running Tag's activity-now.json while the window is open, and name every Tag's mood.
 * `url` turns a local path into something fetch can read, or null where it can't (a browser preview).
 */
export function useMoods(rows: TagRow[], { busy, problems, activity, url }: {
  busy: Set<string>; problems: Record<string, string | null>; activity: Record<string, ActivityItem[]>;
  url: (path: string, revision: string) => string | null;
}): MoodOf {
  const [nows, setNows] = useState<Record<string, Now>>({});
  const [moments, setMoments] = useState<Record<string, Mood>>({});
  const previous = useRef<Record<string, Now>>({});
  const watched = rows.filter((row) => row.state === "running" && row.activity_now)
    .map((row) => `${row.id}\t${row.activity_now}`).join("\n");

  useEffect(() => {
    if (!watched) { previous.current = {}; setNows({}); return; }
    const targets = watched.split("\n").map((line) => line.split("\t") as [string, string]);
    let live = true;
    const timers = new Set<ReturnType<typeof setTimeout>>();
    const read = async () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      const next: Record<string, Now> = {};
      await Promise.all(targets.map(async ([id, path]) => {
        const address = url(path, String(Date.now()));
        if (!address) return;
        try {
          const response = await fetch(address, { cache: "no-store" });
          const now = response.ok ? parseNow(await response.json()) : null;
          if (now) next[id] = now;
        } catch {
          // Not written yet, or unreadable: the row and the activity read still show the Tag.
        }
      }));
      if (!live) return;
      for (const [id, now] of Object.entries(next)) {
        const happened = moment(previous.current[id] ?? null, now);
        if (!happened) continue;
        setMoments((all) => ({ ...all, [id]: happened }));
        const timer = setTimeout(() => {
          timers.delete(timer);
          setMoments((all) => { const { [id]: _done, ...rest } = all; return rest; });
        }, PERIOD[happened] * 1000);
        timers.add(timer);
      }
      previous.current = next;
      setNows(next);
    };
    void read();
    const timer = setInterval(() => void read(), NOW_SECONDS * 1000);
    return () => { live = false; clearInterval(timer); for (const t of timers) clearTimeout(t); };
  }, [watched, url]);

  return useMemo(() => (row: TagRow) => {
    const base = baseMood(row, { busy: busy.has(row.id), problem: problems[row.id] ?? null,
      now: nows[row.id] ?? null, latest: activity[row.id]?.[0] });
    // A moment plays over working or idle, never over a Tag that just went off or broke.
    return (base === "work" || base === "idle") && moments[row.id] ? moments[row.id] : base;
  }, [busy, problems, nows, activity, moments]);
}
