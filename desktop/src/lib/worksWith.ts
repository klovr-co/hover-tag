// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Works with: the Tags and apps that can give a Tag tasks, as Tag detail edits
// them through `tag NAME settings works-with`. The owner ticks everyone in a
// picker; nothing is ticked for them. Saving restarts a running Tag, like its model.
import { useCallback, useEffect, useRef, useState } from "react";
import type { Bridge } from "./bridge";
import { failureLine } from "./tags";

export type WorksWithKind = "tag" | "app";
export type WorksWithEntry = { id: string; name: string | null; kind: WorksWithKind };
/** Someone the owner could pick; `tag` is the Tag's ID when it is one of theirs. */
export type WorksWithCandidate = WorksWithEntry & { tag?: string };
export type WorksWithReport = {
  works_with: WorksWithEntry[]; error: string | null;
  candidates?: WorksWithCandidate[]; slack_error?: string | null;
  restart_required?: boolean; restarted?: boolean;
};

/** Slack member IDs (U…, W…) or bot IDs (B…), as the CLI accepts them. */
export const SLACK_ID = /^[BUW][A-Z0-9]{2,}$/;

const parse = (stdout: string) => JSON.parse(stdout.slice(stdout.indexOf("{"))) as WorksWithReport;

export function useWorksWith(api: Bridge, tag: string, { onSaved }: { onSaved?: (text: string) => void } = {}) {
  const [report, setReport] = useState<WorksWithReport | null>(null);
  const [candidates, setCandidates] = useState<WorksWithCandidate[] | null>(null);
  const [slackError, setSlackError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const current = useRef(tag);
  current.current = tag;

  const run = useCallback(async (args: string[], fallback: string) => {
    const id = tag;
    const result = await api.tag([id, "settings", "works-with", ...args, "--json"]);
    if (current.current !== id) return null;
    if (result.code !== 0) { setError(failureLine(result, fallback)); return null; }
    setError("");
    return parse(result.stdout);
  }, [api, tag]);

  useEffect(() => {
    setReport(null); setCandidates(null); setSlackError(null); setError(""); setSaving(false);
    run([], "Couldn't read who this Tag works with.").then((r) => r && setReport(r), (e) => setError(String(e)));
  }, [run]);

  /** Reads everyone the owner could pick: their Tags here, then the workspace's apps. */
  const loadCandidates = useCallback(async () => {
    try {
      const found = await run(["candidates"], "Couldn't list Tags and apps.");
      if (!found) return;
      setReport(found);
      setCandidates(found.candidates ?? []);
      setSlackError(found.slack_error ?? null);
    } catch (e) { setError(String(e)); }
  }, [run]);

  /** Saves exactly these IDs, restarting a running Tag; resolves whether it worked. */
  const save = async (ids: string[], restart: boolean) => {
    setSaving(true);
    try {
      const done = await run(["set", ...ids, ...(restart ? ["--restart"] : [])], "Couldn't save who this Tag works with.");
      if (!done) return false;
      setReport(done);
      onSaved?.(done.restarted ? "restarted. The change applies now." : done.restart_required
        ? "saved the change. Restart it to apply." : "saved the change. It applies the next time it starts.");
      return true;
    } catch (e) { setError(String(e)); return false; }
    finally { if (current.current === tag) setSaving(false); }
  };

  return { report, candidates, slackError, saving, error, loadCandidates, save };
}
