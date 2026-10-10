// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// The shared memory service, as `tag memory status --json` reports it.
import { parseJSON } from "./protocol";

export interface MemoryStatus {
  healthy: boolean;
  managed: boolean;
  keepRunning: boolean;
  runningTags: string[];
  /** Null when this Tag is too old to say. */
  embedding: { provider: string; local: boolean } | null;
}

export function parseMemory(output: string): MemoryStatus {
  const status = parseJSON<{
    healthy?: boolean; managed?: boolean; keep_running?: boolean; running_tags?: string[];
    embedding?: { provider?: string; local?: boolean } | null;
  }>(output);
  return {
    healthy: !!status.healthy,
    managed: !!status.managed,
    keepRunning: !!status.keep_running,
    runningTags: status.running_tags ?? [],
    embedding: status.embedding ? { provider: status.embedding.provider ?? "", local: !!status.embedding.local } : null,
  };
}

/** Where search embeddings are computed: the one fact people choose at install time. */
export function embeddingText(memory: MemoryStatus) {
  if (!memory.embedding) return "";
  return memory.embedding.local ? "Search model runs on this computer." : `Search model is hosted (${memory.embedding.provider}).`;
}

export function memoryDetail(memory: MemoryStatus) {
  const state = memory.healthy ? "Running." : "Not running.";
  const restarts = memory.keepRunning ? "Restarts itself if it stops." : "Won't restart if it stops; turn on Keep Tags running for that.";
  return [state, restarts, embeddingText(memory)].filter(Boolean).join(" ");
}
