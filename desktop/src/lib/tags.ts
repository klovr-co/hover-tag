// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// The list of Tags on this computer, kept current for the window and the tray.
import { useCallback, useEffect, useRef, useState } from "react";
import type { Bridge, RunResult } from "./bridge";
import { groups, isDraft, parseJSON, parseList, status, title, type TagRow } from "./protocol";

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

  const run = useCallback(async (key: string, args: string[], fallback: string) => {
    if (!api) return false;
    mark(key, true);
    try {
      const result = await api.tag(args);
      setError(result.code === 0 ? "" : failureLine(result, fallback));
      return result.code === 0;
    } catch (error) {
      // Tag couldn't be run at all, for example after it was uninstalled.
      setError(`${fallback} ${String(error)}`);
      return false;
    } finally {
      mark(key, false);
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

  const rename = useCallback((row: TagRow, name: string) =>
    run(row.id, [row.id, "rename", name.trim(), "--json"], "Rename failed."), [run]);

  /** Change the Tag's one-line Slack description; an empty one clears it. */
  const describe = useCallback((row: TagRow, description: string) =>
    run(row.id, [row.id, "describe", description.trim(), "--json"], "Couldn't change the description."), [run]);

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
    refresh, refreshAutostart, toggle, workspace, all, rename, describe, discardDrafts, remove, setAutostart,
  };
}

export type Tags = ReturnType<typeof useTags>;
