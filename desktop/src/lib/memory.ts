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
  id: string;
  label: string;
  detail: string;
  /** Needs an API key, and sends the text it indexes to that provider. */
  hosted: boolean;
  /** A key for this provider is already stored, so switching to it needs none. */
  keySaved: boolean;
}

export interface Embedding {
  provider: string;
  local: boolean;
  /** The id of the choice in use. */
  current: string;
  choices: EmbeddingChoice[];
  /** Memory still has to index your sources into the new search model's index. */
  reindexPending: boolean;
  problem: string;
}

export interface EmbeddingResult { ok: boolean; error: string; reindexing: boolean; note: string }

export function parseMemory(output: string): MemoryStatus {
  const status = parseJSON<{
    healthy?: boolean; managed?: boolean; keep_running?: boolean; running_tags?: string[];
    embedding?: {
      provider?: string; local?: boolean; current?: string; reindex_pending?: boolean; problem?: string;
      choices?: { id: string; label: string; detail?: string; hosted?: boolean; key_saved?: boolean }[];
    } | null;
  }>(output);
  return {
    healthy: !!status.healthy,
    managed: !!status.managed,
    keepRunning: !!status.keep_running,
    runningTags: status.running_tags ?? [],
    // An older Tag without the choice list can't offer a switch, so it reports none.
    embedding: status.embedding?.choices?.length ? {
      provider: status.embedding.provider ?? "", local: !!status.embedding.local,
      current: status.embedding.current ?? (status.embedding.local ? "onnx" : status.embedding.provider ?? ""),
      choices: status.embedding.choices.map((c) => ({ id: c.id, label: c.label, detail: c.detail ?? "", hosted: !!c.hosted, keySaved: !!c.key_saved })),
      reindexPending: !!status.embedding.reindex_pending, problem: status.embedding.problem ?? "",
    } : status.embedding ? {
      provider: status.embedding.provider ?? "", local: !!status.embedding.local, current: status.embedding.local ? "onnx" : status.embedding.provider ?? "",
      choices: [], reindexPending: false, problem: "",
    } : null,
  };
}

/** Where search embeddings are computed: the one fact people choose at install time. */
export function embeddingText(memory: MemoryStatus) {
  if (!memory.embedding) return "";
  if (memory.embedding.local) return "Search model runs on this computer.";
  const label = memory.embedding.choices.find((c) => c.id === memory.embedding?.current)?.label ?? memory.embedding.provider;
  return `Search model is hosted (${label}).`;
}

/**
 * `tag memory embedding local|openai`. The key goes over stdin only and is never part of the command,
 * so it can't show up in a process list or a log.
 */
export function setEmbedding(api: Bridge, choice: string, key: string): Promise<EmbeddingResult> {
  const failed = (error: string): EmbeddingResult => ({ ok: false, error, reindexing: false, note: "" });
  return new Promise((resolve) => {
    let result: EmbeddingResult | null = null;
    const hosted = choice !== "local" && choice !== "onnx";
    const args = ["memory", "embedding", hosted ? choice : "local", ...(hosted ? ["--stdin"] : []), "--json"];
    api.setup(args, (line) => {
      if (!line.trim().startsWith("{")) return;
      try {
        const report = JSON.parse(line) as { ok?: boolean; error?: string; reindexing?: boolean; note?: string };
        result = report.ok === false ? failed(report.error || "Couldn't change the search model.")
          : { ok: true, error: "", reindexing: !!report.reindexing, note: report.note ?? "" };
      } catch { /* not a result line */ }
    }, (code, stderr) => resolve(result ?? failed(
      stderr.trim().split("\n").pop()?.replace(/^Error: /, "") || `Tag stopped unexpectedly (${code}).`)))
      .then((session) => { if (hosted) session.send({ api_key: key }); })
      .catch((e) => resolve(failed(String(e))));
  });
}

export function memoryDetail(memory: MemoryStatus) {
  const state = memory.healthy ? "Running." : "Not running.";
  const restarts = memory.keepRunning ? "Restarts itself if it stops." : "Won't restart if it stops; turn on Keep Tags running for that.";
  return [state, restarts, embeddingText(memory)].filter(Boolean).join(" ");
}
