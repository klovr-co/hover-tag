import { describe, expect, it } from "vitest";
import { memoryDetail, parseMemory } from "./memory";

const report = (extra: object) => JSON.stringify({ schema_version: 1, managed: true, healthy: true, running_tags: [], keep_running: true, ...extra });

describe("memory status", () => {
  it("reads the service state and where the search model runs", () => {
    const memory = parseMemory(report({ embedding: { provider: "onnx", model: "m", local: true } }));
    expect(memory).toMatchObject({ healthy: true, managed: true, keepRunning: true, embedding: { provider: "onnx", local: true } });
    expect(memoryDetail(memory)).toBe("Running. Restarts itself if it stops. Search model runs on this computer.");
  });

  it("says when the search model is hosted, and when memory won't restart", () => {
    const memory = parseMemory(report({ healthy: false, keep_running: false, embedding: { provider: "openai", local: false } }));
    expect(memoryDetail(memory)).toBe("Not running. Won't restart if it stops; turn on Keep Tags running for that. Search model is hosted (openai).");
  });

  it("copes with an older Tag that doesn't report the search model", () => {
    const memory = parseMemory(report({}));
    expect(memory.embedding).toBeNull();
    expect(memoryDetail(memory)).toBe("Running. Restarts itself if it stops.");
  });
});
