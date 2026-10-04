// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// A Tag's default model and thinking level, as Tag detail and Settings →
// AI & models edit them: picked locally, then saved together, restarting a
// running Tag only after a click.
import { useCallback, useEffect, useRef, useState } from "react";
import type { Bridge } from "./bridge";
import { aiArgs, findModel, parseModels, parseStatus, type AIModels, type AIStatus, type ModelEntry } from "./ai";
import { effortLabel } from "./home";
import { failureLine } from "./tags";

/** A level the new model doesn't offer falls back to that model's own default. */
export function fitEffort(entry: Pick<ModelEntry, "efforts" | "default_effort"> | null | undefined, effort: string | null) {
  const levels = entry?.efforts ?? [];
  return effort && levels.includes(effort) ? effort : entry?.default_effort ?? null;
}

/** "Opus 5.5 · High thinking", as save bars and toasts say it. */
export const choiceText = (label: string, effort: string | null) => `${label}${effort ? ` · ${effortLabel(effort)} thinking` : ""}`;

export type SaveState = "idle" | "saving" | "restarting" | "saved";

export function useModelChoice(api: Bridge, tag: string, { onSaved }: { onSaved?: (text: string) => void } = {}) {
  const [report, setReport] = useState<AIStatus | null>(null);
  const [models, setModels] = useState<AIModels | null>(null);
  const [model, setModel] = useState<string | null>(null);
  const [effort, setEffort] = useState<string | null>(null);
  const [save, setSave] = useState<SaveState>("idle");
  const [error, setError] = useState("");
  const current = useRef(tag);
  current.current = tag;

  const load = useCallback(async () => {
    const id = tag;
    setError("");
    try {
      const status = await api.tag(aiArgs(id, "--json"));
      if (current.current !== id) return;
      if (status.code !== 0) { setError(failureLine(status, "Couldn't check this Tag's AI.")); return; }
      const next = parseStatus(status.stdout);
      setReport(next);
      if (!next.usable.length) { setModels(null); return; }
      const listed = await api.tag(aiArgs(id, "models", "--json"));
      if (current.current !== id) return;
      if (listed.code === 0) setModels(parseModels(listed.stdout));
      else setError(failureLine(listed, "Couldn't load models from your accounts."));
    } catch (e) {
      setError(String(e));
    }
  }, [api, tag]);

  useEffect(() => {
    setReport(null); setModels(null); setModel(null); setEffort(null); setSave("idle");
    void load();
  }, [load]);

  const savedModel = report?.default_model.value ?? null;
  const savedEffort = report?.default_effort ?? null;
  const value = model ?? savedModel;
  const level = model || effort ? effort : savedEffort;
  const entry = models && value ? findModel(models.groups, value)?.entry ?? null : null;
  const dirty = !!report && (value !== savedModel || level !== savedEffort);

  const pick = (next: string) => {
    const picked = models ? findModel(models.groups, next)?.entry ?? null : null;
    setModel(next);
    setEffort(fitEffort(picked, level));
    setSave("idle");
  };
  const pickEffort = (next: string) => {
    setModel(value);
    setEffort(next);
    setSave("idle");
  };
  const discard = () => { setModel(null); setEffort(null); };

  const commit = async (restart: boolean) => {
    if (!value || !report) return;
    setSave(restart ? "restarting" : "saving");
    setError("");
    const args = aiArgs(tag, "model", value, ...(level ? ["--effort", level] : ["--effort", "default"]), ...(restart ? ["--restart"] : []), "--json");
    const result = await api.tag(args);
    if (result.code !== 0) { setSave("idle"); setError(failureLine(result, "Couldn't save the default model.")); return; }
    const saved = JSON.parse(result.stdout.slice(result.stdout.indexOf("{"))) as {
      default_model: AIStatus["default_model"]; default_effort?: string | null; restarted?: boolean;
    };
    setReport((r) => r && { ...r, default_model: saved.default_model, default_effort: saved.default_effort ?? null,
      effort_chosen: !!level, effort_levels: entry?.efforts ?? r.effort_levels });
    setModel(null);
    setEffort(null);
    const text = choiceText(saved.default_model.label, saved.default_effort ?? null);
    if (restart) { setSave("idle"); onSaved?.(saved.restarted ? `restarted with ${text}` : ""); }
    else {
      setSave("saved");
      onSaved?.("");
      setTimeout(() => setSave((s) => (s === "saved" ? "idle" : s)), 2600);
    }
  };

  return { report, models, value, level, entry, dirty, save, error, pick, pickEffort, discard, commit, reload: load };
}

export type ModelChoice = ReturnType<typeof useModelChoice>;
