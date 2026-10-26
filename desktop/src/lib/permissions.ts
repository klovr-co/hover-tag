// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// A Tag's Codex and Claude permissions, as Tag detail edits them: each agent
// keeps its own settings, picked locally, then saved together, restarting a
// running Tag only after a click, like its model. Each agent's own rules for the
// Tag change at once and apply from its next request, with no restart.
import { useCallback, useEffect, useRef, useState } from "react";
import type { Bridge } from "./bridge";
import { aiArgs, type AllTagsRules, type PermissionOption, type Permissions } from "./ai";
import type { SaveState } from "./model";
import { failureLine } from "./tags";

/** The CLI's name for each setting, and where it lives in the report. */
const SETTINGS = {
  "codex-sandbox": { backend: "codex", key: "sandbox" },
  "codex-approvals": { backend: "codex", key: "approvals" },
  "codex-reviewer": { backend: "codex", key: "reviewer" },
  "claude-mode": { backend: "claude", key: "mode" },
  "claude-chrome": { backend: "claude", key: "chrome" },
} as const;
export type Setting = keyof typeof SETTINGS;
export type RuleBackend = "codex" | "claude";
export type RuleAction = "allow" | "block" | "remove";

export function savedValue(report: Permissions, setting: Setting): string {
  const { backend, key } = SETTINGS[setting];
  return (report[backend] as unknown as Record<string, string>)[key];
}

export function optionsFor(report: Permissions, setting: Setting): PermissionOption[] {
  const { backend, key } = SETTINGS[setting];
  return (report.options[backend] as Record<string, PermissionOption[]>)[key];
}

export function usePermissions(api: Bridge, tag: string, loaded: Permissions | null | undefined,
  { onSaved, onRuleChanged }: { onSaved?: (text: string) => void; onRuleChanged?: (action: RuleAction, rule: string) => void } = {}) {
  const [saved, setSaved] = useState<Permissions | null>(null);
  const [draft, setDraft] = useState<Partial<Record<Setting, string>>>({});
  const [save, setSave] = useState<SaveState>("idle");
  const [error, setError] = useState("");
  // The rule being changed, as `backend:value`, or `backend:` while one is added.
  const [ruleBusy, setRuleBusy] = useState<string | null>(null);
  const current = useRef(tag);
  current.current = tag;

  useEffect(() => { setSaved(null); setDraft({}); setSave("idle"); setError(""); setRuleBusy(null); }, [tag]);
  // A newly loaded AI status (read when the Tag opens) replaces what this view last saved. Saving a
  // model keeps the same permissions, so this doesn't run then, and changes made elsewhere, like
  // Always allow in Slack, show the next time the Tag opens.
  useEffect(() => { setSaved(null); }, [loaded]);

  const report = saved ?? loaded ?? null;
  const value = (setting: Setting) => draft[setting] ?? (report ? savedValue(report, setting) : "");
  const changed = report ? (Object.keys(draft) as Setting[]).filter((s) => draft[s] !== savedValue(report, s)) : [];
  const dirty = changed.length > 0;

  const pick = useCallback((setting: Setting, next: string) => {
    setDraft((d) => ({ ...d, [setting]: next }));
    setSave("idle");
  }, []);
  const discard = useCallback(() => setDraft({}), []);

  const commit = async (restart: boolean) => {
    if (!dirty) return;
    const id = tag;
    setSave(restart ? "restarting" : "saving");
    setError("");
    let done: { permissions: Permissions; restarted?: boolean };
    try {
      const result = await api.tag(aiArgs(id, "permissions", ...changed.map((s) => `${s}=${draft[s]}`),
        ...(restart ? ["--restart"] : []), "--json"));
      if (current.current !== id) return;
      if (result.code !== 0) { setSave("idle"); setError(failureLine(result, "Couldn't save the permissions.")); return; }
      done = JSON.parse(result.stdout.slice(result.stdout.indexOf("{")));
    } catch (e) {
      if (current.current === id) { setSave("idle"); setError(String(e)); }
      return;
    }
    setSaved(done.permissions);
    setDraft({});
    if (restart) { setSave("idle"); onSaved?.(done.restarted ? "restarted with the new permissions" : ""); }
    else {
      setSave("saved");
      onSaved?.("");
      setTimeout(() => setSave((s) => (s === "saved" ? "idle" : s)), 2600);
    }
  };

  /** Allow, block, or remove one of the Tag's own rules; resolves whether it worked. */
  const changeRule = async (backend: RuleBackend, action: RuleAction, rule: string) => {
    const id = tag;
    setRuleBusy(`${backend}:${action === "remove" ? rule : ""}`);
    setError("");
    try {
      const result = await api.tag(aiArgs(id, "permissions", action, backend, rule, "--json"));
      if (current.current !== id) return false;
      if (result.code !== 0) { setError(failureLine(result, "Couldn't change the rule.")); return false; }
      const done = JSON.parse(result.stdout.slice(result.stdout.indexOf("{"))) as { permissions: Permissions };
      setSaved(done.permissions);
      onRuleChanged?.(action, rule);
      return true;
    } catch (e) {
      if (current.current === id) setError(String(e));
      return false;
    } finally {
      // Another Tag opened meanwhile already cleared it.
      if (current.current === id) setRuleBusy(null);
    }
  };

  return { report, value, dirty, save, error, pick, discard, commit, changeRule, ruleBusy };
}

export type PermissionsChoice = ReturnType<typeof usePermissions>;

/** Settings → AI connections: the rules every Tag follows, changed at once. */
export function useAllTagsRules(api: Bridge, { onChanged }: { onChanged?: (action: RuleAction, rule: string) => void } = {}) {
  const [rules, setRules] = useState<AllTagsRules["rules"] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const run = useCallback(async (args: string[], fallback: string) => {
    try {
      const result = await api.tag(["settings", "ai", "rules", ...args, "--json"]);
      if (result.code !== 0) { setError(failureLine(result, fallback)); return false; }
      setRules((JSON.parse(result.stdout.slice(result.stdout.indexOf("{"))) as AllTagsRules).rules);
      setError("");
      return true;
    } catch (e) { setError(String(e)); return false; }
  }, [api]);
  useEffect(() => { void run([], "Couldn't read the rules for all Tags."); }, [run]);
  /** Allow, block, or remove one rule for every Tag; resolves whether it worked. */
  const changeRule = async (backend: RuleBackend, action: RuleAction, rule: string) => {
    setBusy(`${backend}:${action === "remove" ? rule : ""}`);
    try {
      const ok = await run([action, backend, rule], "Couldn't change the rule.");
      if (ok) onChanged?.(action, rule);
      return ok;
    } finally { setBusy(null); }
  };
  return { rules, busy, error, changeRule };
}
