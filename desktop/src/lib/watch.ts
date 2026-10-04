// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Keeps Home's quiet line and rows current: each Tag's AI connections
// (`tag NAME settings ai --json`) and its latest activity (`tag NAME logs --json`).
import { useCallback, useEffect, useRef, useState } from "react";
import type { Bridge } from "./bridge";
import { aiArgs, parseStatus, type AIStatus } from "./ai";
import { aiProblem, liveRows, type ActivityItem } from "./home";
import { parseJSON, type TagRow } from "./protocol";

/** AI sign-ins change rarely and each check asks Codex and Claude, so check sparingly. */
export const AI_CHECK_SECONDS = 300;
export const ACTIVITY_SECONDS = 60;

export interface Watch {
  reports: Record<string, AIStatus>;
  problems: Record<string, string | null>;
  activity: Record<string, ActivityItem[]>;
  /** Check the AI connections again now, for example after Settings changed them. */
  recheck: () => void;
}

export async function readActivity(api: Bridge, id: string, limit = 1): Promise<ActivityItem[] | null> {
  const result = await api.tag([id, "logs", "--json", "--limit", String(limit)]);
  if (result.code !== 0) return null;
  const items = parseJSON<{ activity?: ActivityItem[] }>(result.stdout).activity;
  return Array.isArray(items) ? items : null;
}

export function useWatch(api: Bridge | null, rows: TagRow[], { ai, activity: logs }: { ai: boolean; activity: boolean }): Watch {
  const [reports, setReports] = useState<Record<string, AIStatus>>({});
  const [activity, setActivity] = useState<Record<string, ActivityItem[]>>({});
  const [round, setRound] = useState(0);
  const ids = liveRows(rows).filter((row) => row.valid).map((row) => row.id).join(",");
  const sequence = useRef(0);

  useEffect(() => {
    if (!api || !ai || !ids) return;
    let live = true;
    const check = async () => {
      const mine = ++sequence.current;
      const next: Record<string, AIStatus> = {};
      // One at a time: every check starts Codex's and Claude's own status commands.
      for (const id of ids.split(",")) {
        try {
          const result = await api.tag(aiArgs(id, "--json"));
          if (result.code === 0) next[id] = parseStatus(result.stdout);
        } catch {
          // A Tag that can't report stays without a line; its row shows what list says.
        }
      }
      // Only the newest check counts.
      if (live && mine === sequence.current) setReports(next);
    };
    void check();
    const timer = setInterval(() => void check(), AI_CHECK_SECONDS * 1000);
    return () => { live = false; clearInterval(timer); };
  }, [api, ai, ids, round]);

  useEffect(() => {
    if (!api || !logs || !ids) return;
    let live = true;
    const read = async () => {
      const entries = await Promise.all(ids.split(",").map(async (id) => {
        try { return [id, (await readActivity(api, id)) ?? []] as const; } catch { return [id, []] as const; }
      }));
      if (live) setActivity(Object.fromEntries(entries));
    };
    void read();
    const timer = setInterval(() => void read(), ACTIVITY_SECONDS * 1000);
    return () => { live = false; clearInterval(timer); };
  }, [api, logs, ids]);

  const problems = Object.fromEntries(Object.entries(reports).map(([id, report]) => [id, aiProblem(report)]));
  const recheck = useCallback(() => setRound((n) => n + 1), []);
  return { reports, problems, activity, recheck };
}
