import { describe, expect, it, vi } from "vitest";
import { demoBridge } from "./bridge";
import { memoryDetail, parseMemory, setEmbedding } from "./memory";

const report = (extra: object) => JSON.stringify({ schema_version: 1, managed: true, healthy: true, running_tags: [], keep_running: true, ...extra });
const choices = [
  { id: "onnx", label: "Default", detail: "Built in", custom: false, model: "", base_url: "", key_saved: false },
  { id: "custom", label: "Custom", detail: "Any endpoint", custom: true, model: "nomic-embed-text", base_url: "http://localhost:11434/v1", key_saved: true },
];

describe("memory status", () => {
  it("reads the service state and what the search model is", () => {
    const memory = parseMemory(report({ embedding: { provider: "onnx", model: "m", local: true, current: "onnx", choices } }));
    expect(memory).toMatchObject({ healthy: true, managed: true, keepRunning: true, embedding: { local: true, current: "onnx" } });
    expect(memory.embedding?.choices[1]).toEqual({ id: "custom", label: "Custom", detail: "Any endpoint", custom: true,
      model: "nomic-embed-text", baseUrl: "http://localhost:11434/v1", keySaved: true });
    expect(memoryDetail(memory)).toBe("Running. Restarts itself if it stops. Search model: default, built in.");
  });

  it("names a custom model, and says when memory won't restart", () => {
    const memory = parseMemory(report({ healthy: false, keep_running: false,
      embedding: { provider: "openai", model: "nomic-embed-text", local: false, current: "custom", choices } }));
    expect(memoryDetail(memory)).toBe("Not running. Won't restart if it stops; turn on Keep Tags running for that. Search model: custom (nomic-embed-text).");
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

  it("passes the model and address as options and the key over stdin only", async () => {
    const { api, send, calls } = bridge([JSON.stringify({ ok: true, reindexing: true })]);
    const result = await setEmbedding(api, "custom", "sk-synthetic", { model: "nomic-embed-text", baseUrl: "http://localhost:11434/v1" });
    expect(result).toMatchObject({ ok: true, reindexing: true });
    expect(calls[0]).toEqual(["memory", "embedding", "custom", "--embedding-model", "nomic-embed-text",
      "--base-url", "http://localhost:11434/v1", "--stdin", "--json"]);
    expect(JSON.stringify(calls)).not.toContain("sk-synthetic");
    expect(send).toHaveBeenCalledWith({ api_key: "sk-synthetic" });
  });

  it("sends an empty key when the endpoint needs none", async () => {
    const { api, send } = bridge([JSON.stringify({ ok: true })]);
    await setEmbedding(api, "custom", "", { model: "m", baseUrl: "http://localhost:1/v1" });
    expect(send).toHaveBeenCalledWith({ api_key: "" });
  });

  it("switches back to the default without a key", async () => {
    const { api, send, calls } = bridge([JSON.stringify({ ok: true, reindexing: false, note: "Takes effect the next time memory starts." })]);
    const result = await setEmbedding(api, "local", "");
    expect(result.note).toBe("Takes effect the next time memory starts.");
    expect(calls[0]).toEqual(["memory", "embedding", "local", "--json"]);
    expect(send).not.toHaveBeenCalled();
  });

  it("reports why Tag refused, from its JSON or its error line", async () => {
    expect(await setEmbedding(bridge([JSON.stringify({ ok: false, error: "localhost rejected that key." })], 1).api, "custom", "k", { model: "m", baseUrl: "u" }))
      .toMatchObject({ ok: false, error: "localhost rejected that key." });
    expect(await setEmbedding(bridge([], 1, "Error: Enter the embedding model's name.").api, "custom", "k"))
      .toMatchObject({ ok: false, error: "Enter the embedding model's name." });
  });
});
