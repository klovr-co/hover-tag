// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// The list of Tags on this computer, kept current for the window and the tray.
import { useCallback, useEffect, useRef, useState } from "react";
import type { Bridge, RunResult } from "./bridge";
import { groups, isDraft, parseJSON, parseList, status, title, type TagRow } from "./protocol";
import { startResult } from "./start";

export const REFRESH_SECONDS = 30;

/** The last line Tag printed is the one that says what went wrong. */
export function failureLine(result: RunResult, fallback: string) {
  // JSON commands report a failure as {"ok": false, "error": "…"}; say just the message.
  const start = result.stdout.indexOf("{");
  if (start >= 0) {
    try {
      const reported = JSON.parse(result.stdout.slice(start)) as { error?: unknown };
      if (typeof reported.error === "string" && reported.error) return reported.error;
    } catch {
      // Not one JSON object; fall back to the last line.
    }
  }
  const lines = (result.stderr || result.stdout).trim().split("\n").filter(Boolean);
  return (lines.pop() ?? fallback).replace("tag_cli.py: error: ", "").replace(/^Error: /, "");
}

/** Start a Tag. Returns what went wrong, or "". Tag itself waits out a setup, start or stop that holds the Tag. */
export async function startTag(api: Bridge, id: string, onLine?: (line: string) => void) {
  if (onLine) return followStart(api, id, onLine);
  try {
    const result = await api.tag([id, "start"]);
    return result.code === 0 ? "" : failureLine(result, "Couldn't start this Tag.");
  } catch (error) {
    return `Couldn't start this Tag. ${String(error)}`;
  }
}

/** Start a Tag with `tag NAME start --json`, passing each progress line on as it happens. */
function followStart(api: Bridge, id: string, onLine: (line: string) => void) {
  return new Promise<string>((resolve) => {
    let outcome: string | null = null;
    api.follow([id, "start"], (line) => {
      const result = startResult(line);
      if (result) outcome = result.ok ? "" : result.error || "Couldn't start this Tag.";
      else onLine(line);
    }, (code, stderr) => {
      resolve(outcome ?? (code === 0 ? "" : failureLine({ code, stdout: "", stderr }, "Couldn't start this Tag.")));
    }).catch((error) => resolve(`Couldn't start this Tag. ${String(error)}`));
  });
}

/** Tags that went offline without anyone stopping them, for a notification. */
export function droppedTags(before: TagRow[], after: TagRow[], expected: Set<string>) {
  const was = new Set(before.filter((r) => r.state === "running").map((r) => r.id));
  return after.filter((r) => was.has(r.id) && r.state !== "running" && !expected.has(r.id));
}

export function useTags(api: Bridge | null, enabled: boolean) {
  const [rows, setRows] = useState<TagRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [refreshError, setRefreshError] = useState("");
  const [keepRunning, setKeepRunning] = useState(false);
  const previous = useRef<TagRow[]>([]);
  const stopping = useRef(new Set<string>());
  const drafts = useRef<string[]>([]);

  const mark = (key: string, on: boolean) =>
    setBusy((current) => {
      const next = new Set(current);
      if (on) next.add(key); else next.delete(key);
      return next;
    });

  const refresh = useCallback(async () => {
    if (!api) return;
    try {
      const result = await api.tag(["list", "--json"]);
      if (result.code !== 0) throw new Error(failureLine(result, "Couldn't read your Tags."));
      const all = parseList(result.stdout);
      // Setups that never reached Slack aren't shown; they are set aside once nothing is setting them up.
      drafts.current = all.filter(isDraft).map((row) => row.id);
      const next = all.filter((row) => !isDraft(row));
      for (const row of droppedTags(previous.current, next, stopping.current)) {
        void api.notify(`${title(row)} went offline`, "Open Tag to see what happened, or start it again.");
      }
      stopping.current.clear();
      previous.current = next;
      setRows(next);
      setLoaded(true);
      setRefreshError("");
    } catch (cause) {
      const detail = cause instanceof Error ? cause.message : String(cause ?? "");
      const fallback = "Couldn't read your Tags.";
      setRefreshError(detail && detail !== fallback ? `${fallback} ${detail}` : fallback);
    }
  }, [api]);

  const refreshAutostart = useCallback(async () => {
    if (!api) return;
    try {
      const result = await api.tag(["autostart", "status", "--json"]);
      if (result.code === 0) setKeepRunning(!!parseJSON<{ enabled: boolean }>(result.stdout).enabled);
    } catch {
      // An older Tag without autostart: the setting simply shows as off.
    }
  }, [api]);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
    void refreshAutostart();
    const timer = setInterval(() => void refresh(), REFRESH_SECONDS * 1000);
    return () => clearInterval(timer);
  }, [enabled, refresh, refreshAutostart]);

  // The tray shows the same list, even while the window is closed.
  useEffect(() => {
    if (!api || !loaded) return;
    void api.updateTray(groups(rows).flatMap((group) => group.rows.map((row) => ({
      id: row.id, title: title(row), group: groups(rows).length > 1 ? group.label : "",
      running: row.state === "running", needsSetup: status(row) === "setup",
    }))), keepRunning);
  }, [api, rows, loaded, keepRunning]);

  /** Run a Tag command, keeping `key` busy meanwhile. Returns what went wrong, or "". */
  const attempt = useCallback(async (key: string, args: string[], fallback: string) => {
    if (!api) return fallback;
    mark(key, true);
    try {
      const result = await api.tag(args);
      return result.code === 0 ? "" : failureLine(result, fallback);
    } catch (error) {
      // Tag couldn't be run at all, for example after it was uninstalled.
      return `${fallback} ${String(error)}`;
    } finally {
      mark(key, false);
      await refresh();
    }
  }, [api, refresh]);

  /** Run a command whose failure Home shows. */
  const run = useCallback(async (key: string, args: string[], fallback: string) => {
    if (!api) return false;
    const failure = await attempt(key, args, fallback);
    setError(failure);
    return !failure;
  }, [api, attempt]);

  /**
   * Start a Tag another screen asked for, such as the end of setup, so every screen shows it starting.
   * Pass `onLine` to follow the start step by step; only Tags that report `start-progress` can.
   */
  const start = useCallback(async (id: string, onLine?: (line: string) => void) => {
    if (!api) return "";
    mark(id, true);
    try {
      const failure = await startTag(api, id, onLine);
      setError(failure);
      return failure;
    } finally {
      mark(id, false);
      await refresh();
    }
  }, [api, refresh]);

  const toggle = useCallback((row: TagRow) => {
    const action = row.state === "running" ? "stop" : "start";
    if (action === "stop") stopping.current.add(row.id);
    return run(row.id, [row.id, action], `Couldn't ${action} ${title(row)}.`);
  }, [run]);

  const workspace = useCallback((team: string, action: "start" | "stop") => {
    if (action === "stop") rows.filter((r) => r.slack_workspace === team).forEach((r) => stopping.current.add(r.id));
    return run(team, [action, "--workspace", team, "--json"], "Some Tags in this workspace need attention.");
  }, [run, rows]);

  const all = useCallback(async (action: "start" | "stop") => {
    for (const row of rows) {
      if (status(row) === "setup" || (row.state === "running") === (action === "start")) continue;
      await toggle(row);
    }
  }, [rows, toggle]);

  /** Rename the Tag in Slack. Returns what went wrong, or "", for the editor to show in place. */
  const rename = useCallback((row: TagRow, name: string) =>
    attempt(row.id, [row.id, "rename", name.trim(), "--json"], "Rename failed."), [attempt]);

  /** Change the Tag's one-line Slack description; an empty one clears it. Returns what went wrong, or "". */
  const describe = useCallback((row: TagRow, description: string) =>
    attempt(row.id, [row.id, "describe", description.trim(), "--json"], "Couldn't change the description."), [attempt]);

  /** Set aside setups that never reached Slack. Call only when no setup is running. */
  const discardDrafts = useCallback(async () => {
    if (!api || !drafts.current.length) return;
    const ids = drafts.current;
    drafts.current = [];
    for (const id of ids) await api.tag([id, "abandon", "--json"]);
  }, [api]);

  /** Stop a Tag and set its files aside. Pass its App ID to delete its Slack app too (irreversible). */
  const remove = useCallback((row: TagRow, deleteAppId?: string) =>
    run(row.id, [row.id, "remove", "--json", ...(deleteAppId ? ["--delete-app", "--confirm-app", deleteAppId] : [])],
      `Couldn't remove ${title(row)}.`), [run]);

  const setAutostart = useCallback(async (on: boolean) => {
    const ok = await run("autostart", ["autostart", on ? "on" : "off", "--json"], "Couldn't change Keep Tags running.");
    if (ok) setKeepRunning(on);
    return ok;
  }, [run]);

  return {
    rows, loaded, busy, error: error || refreshError, setError, keepRunning,
    refresh, refreshAutostart, start, toggle, workspace, all, rename, describe, discardDrafts, remove, setAutostart,
  };
}

export type Tags = ReturnType<typeof useTags>;
