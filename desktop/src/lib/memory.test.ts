import { describe, expect, it, vi } from "vitest";
import { demoBridge } from "./bridge";
import { memoryDetail, parseMemory, setEmbedding } from "./memory";

const report = (extra: object) => JSON.stringify({ schema_version: 1, managed: true, healthy: true, running_tags: [], keep_running: true, ...extra });

describe("memory status", () => {
  it("reads the service state and where the search model runs", () => {
    const memory = parseMemory(report({ embedding: { provider: "onnx", model: "m", local: true, current: "onnx", choices: [
      { id: "onnx", label: "On this computer", detail: "Private", hosted: false, key_saved: false },
      { id: "openai", label: "OpenAI", detail: "Fast", hosted: true, key_saved: true }] } }));
    expect(memory).toMatchObject({ healthy: true, managed: true, keepRunning: true, embedding: { provider: "onnx", local: true, current: "onnx" } });
    expect(memory.embedding?.choices[1]).toEqual({ id: "openai", label: "OpenAI", detail: "Fast", hosted: true, keySaved: true });
    expect(memoryDetail(memory)).toBe("Running. Restarts itself if it stops. Search model runs on this computer.");
  });

  it("says when the search model is hosted, and when memory won't restart", () => {
    const memory = parseMemory(report({ healthy: false, keep_running: false, embedding: { provider: "openai", local: false, current: "openai", choices: [{ id: "openai", label: "OpenAI", hosted: true }] } }));
    expect(memoryDetail(memory)).toBe("Not running. Won't restart if it stops; turn on Keep Tags running for that. Search model is hosted (OpenAI).");
  });

  it("copes with an older Tag that doesn't report the search model", () => {
    const memory = parseMemory(report({}));
    expect(memory.embedding).toBeNull();
    expect(memoryDetail(memory)).toBe("Running. Restarts itself if it stops.");
  });
});

describe("changing the search model", () => {
  function bridge(lines: string[], code = 0, stderr = "") {
    const api = demoBridge();
    const send = vi.fn();
    const calls: string[][] = [];
    api.setup = async (args, onLine, onExit) => {
      calls.push(args);
      setTimeout(() => { lines.forEach(onLine); onExit(code, stderr); }, 0);
      return { send, stop: () => {} };
    };
    return { api, send, calls };
  }

  it("sends the key over stdin, never in the command", async () => {
    const { api, send, calls } = bridge([JSON.stringify({ ok: true, reindexing: true })]);
    const result = await setEmbedding(api, "openai", "sk-synthetic");
    expect(result).toMatchObject({ ok: true, reindexing: true });
    expect(calls[0]).toEqual(["memory", "embedding", "openai", "--stdin", "--json"]);
    expect(JSON.stringify(calls)).not.toContain("sk-synthetic");
    expect(send).toHaveBeenCalledWith({ api_key: "sk-synthetic" });
  });

  it("sends any hosted provider's key over stdin the same way", async () => {
    const { api, send, calls } = bridge([JSON.stringify({ ok: true })]);
    await setEmbedding(api, "jina", "jina-synthetic");
    expect(calls[0]).toEqual(["memory", "embedding", "jina", "--stdin", "--json"]);
    expect(send).toHaveBeenCalledWith({ api_key: "jina-synthetic" });
  });

  it("switches back to the local model without a key", async () => {
    const { api, send, calls } = bridge([JSON.stringify({ ok: true, reindexing: false, note: "Takes effect the next time memory starts." })]);
    const result = await setEmbedding(api, "local", "");
    expect(result.note).toBe("Takes effect the next time memory starts.");
    expect(calls[0]).toEqual(["memory", "embedding", "local", "--json"]);
    expect(send).not.toHaveBeenCalled();
  });

  it("reports why Tag refused, from its JSON or its error line", async () => {
    expect(await setEmbedding(bridge([JSON.stringify({ ok: false, error: "OpenAI rejected that key." })], 1).api, "openai", "k"))
      .toMatchObject({ ok: false, error: "OpenAI rejected that key." });
    expect(await setEmbedding(bridge([], 1, "Error: Send the OpenAI key on stdin with --stdin").api, "openai", "k"))
      .toMatchObject({ ok: false, error: "Send the OpenAI key on stdin with --stdin" });
  });
});
