// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// The shared memory service, as `tag memory status --json` reports it.
import type { Bridge } from "./bridge";
import { parseJSON } from "./protocol";

export interface MemoryStatus {
  healthy: boolean;
  managed: boolean;
  keepRunning: boolean;
  runningTags: string[];
  /** Null when this Tag is too old to say. */
  embedding: Embedding | null;
}

export interface EmbeddingChoice {
  /** "onnx" is the built-in Default; "custom" is an OpenAI-compatible endpoint the person names. */
  id: string;
  label: string;
  detail: string;
  /** The person gives an address, a model name and, optionally, a key. */
  custom: boolean;
  /** What is configured now, when this is the one in use. */
  model: string;
  baseUrl: string;
  keySaved: boolean;
}

export interface Embedding {
  provider: string;
  local: boolean;
  /** The id of the choice in use. */
  current: string;
  /** What the status reported for the model in use. */
  model: string;
  choices: EmbeddingChoice[];
  /** Memory still has to index your sources into the new search model's index. */
  reindexPending: boolean;
}

export interface EmbeddingResult { ok: boolean; error: string; reindexing: boolean; note: string }

export function parseMemory(output: string): MemoryStatus {
  const status = parseJSON<{
    healthy?: boolean; managed?: boolean; keep_running?: boolean; running_tags?: string[];
    embedding?: {
      provider?: string; model?: string; local?: boolean; current?: string; reindex_pending?: boolean;
      choices?: { id: string; label: string; detail?: string; custom?: boolean; model?: string; base_url?: string; key_saved?: boolean }[];
    } | null;
  }>(output);
  const e = status.embedding;
  return {
    healthy: !!status.healthy,
    managed: !!status.managed,
    keepRunning: !!status.keep_running,
    runningTags: status.running_tags ?? [],
    // An older Tag without the choice list can't offer a switch, so it reports none.
    embedding: e ? {
      provider: e.provider ?? "", model: e.model ?? "", local: !!e.local, current: e.current ?? (e.local ? "onnx" : "custom"),
      choices: (e.choices ?? []).map((c) => ({
        id: c.id, label: c.label, detail: c.detail ?? "", custom: !!c.custom, model: c.model ?? "", baseUrl: c.base_url ?? "", keySaved: !!c.key_saved })),
      reindexPending: !!e.reindex_pending,
    } : null,
  };
}

/** What the search model is, in a few words. */
export function embeddingText(memory: MemoryStatus) {
  if (!memory.embedding) return "";
  return memory.embedding.local ? "Search model: default, built in." : `Search model: custom (${memory.embedding.model}).`;
}

/**
 * `tag memory embedding local|openai`. The key goes over stdin only and is never part of the command,
 * so it can't show up in a process list or a log.
 */
export function setEmbedding(api: Bridge, choice: "local" | "custom", key: string, options: { model?: string; baseUrl?: string } = {}): Promise<EmbeddingResult> {
  const failed = (error: string): EmbeddingResult => ({ ok: false, error, reindexing: false, note: "" });
  return new Promise((resolve) => {
    let result: EmbeddingResult | null = null;
    const custom = choice === "custom";
    const args = ["memory", "embedding", choice,
      ...(custom ? ["--embedding-model", options.model ?? "", "--base-url", options.baseUrl ?? "", "--stdin"] : []), "--json"];
    api.setup(args, (line) => {
      if (!line.trim().startsWith("{")) return;
      try {
        const report = JSON.parse(line) as { ok?: boolean; error?: string; reindexing?: boolean; note?: string };
        result = report.ok === false ? failed(report.error || "Couldn't change the search model.")
          : { ok: true, error: "", reindexing: !!report.reindexing, note: report.note ?? "" };
      } catch { /* not a result line */ }
    }, (code, stderr) => resolve(result ?? failed(
      stderr.trim().split("\n").pop()?.replace(/^Error: /, "") || `Tag stopped unexpectedly (${code}).`)))
      .then((session) => { if (custom) session.send({ api_key: key }); })
      .catch((e) => resolve(failed(String(e))));
  });
}

export function memoryDetail(memory: MemoryStatus) {
  const state = memory.healthy ? "Running." : "Not running.";
  const restarts = memory.keepRunning ? "Restarts itself if it stops." : "Won't restart if it stops; turn on Keep Tags running for that.";
  return [state, restarts, embeddingText(memory)].filter(Boolean).join(" ");
}
