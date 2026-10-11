// The built-in search model's background download, shown on Home while it runs.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { demoBridge } from "../lib/bridge";
import { ModelDownloadNotice } from "./ModelDownload";

afterEach(cleanup);

const MiB = 1048576;
const status = (download: object | null) => ({ code: 0, stderr: "", stdout: JSON.stringify({
  schema_version: 1, healthy: false, managed: false, keep_running: false, running_tags: [],
  embedding: { provider: "onnx", model: "m", local: true, current: "onnx", choices: [], download } }) });
const running = (percent: number, done: number) => ({ state: "downloading", model: "gpahal/bge-m3-onnx-int8", done, total: 560 * MiB, percent,
  message: "Downloading the search model" });

function bridge(...answers: ReturnType<typeof status>[]) {
  const api = demoBridge();
  const calls: string[][] = [];
  let index = 0;
  api.tag = vi.fn(async (args: string[]) => {
    calls.push(args);
    return args[1] === "status" ? answers[Math.min(index++, answers.length - 1)] : { code: 0, stdout: "", stderr: "" };
  });
  return { api, calls };
}

describe("model download notice", () => {
  it("names the model, shows the progress and says search is unavailable until it finishes", async () => {
    const { api } = bridge(status(running(55, 312 * MiB)));
    render(<ModelDownloadNotice api={api} every={5} />);
    expect(await screen.findByText("Downloading the search model")).toBeTruthy();
    expect(screen.getByText(/bge-m3-onnx-int8 · 312 of 560 MB/)).toBeTruthy();
    expect(screen.getByText(/Tag works meanwhile, but memory search isn't available until it finishes/)).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("55");
  });

  it("keeps updating while it downloads, then says search is ready", async () => {
    const { api } = bridge(status(running(10, 56 * MiB)), status(running(60, 336 * MiB)),
      status({ state: "ready", model: "m", done: 1, total: 1, percent: 100, message: "" }));
    render(<ModelDownloadNotice api={api} every={5} />);
    await screen.findByText(/56 of 560 MB/);
    await screen.findByText(/336 of 560 MB/);
    expect(await screen.findByText("Memory search is ready")).toBeTruthy();
  });

  it("stops asking once nothing is downloading", async () => {
    const { api, calls } = bridge(status(null));
    render(<ModelDownloadNotice api={api} every={5} />);
    await waitFor(() => expect(calls.length).toBe(1));
    await new Promise((r) => setTimeout(r, 40));
    expect(calls.length).toBe(1);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("says nothing for a custom model or a Tag too old to report a download", async () => {
    const { api } = bridge({ code: 0, stderr: "", stdout: JSON.stringify({ healthy: true }) });
    render(<ModelDownloadNotice api={api} every={5} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("offers Try again when the download stopped, and starts it again", async () => {
    const { api, calls } = bridge(
      status({ state: "failed", model: "m", done: 0, total: 0, percent: 0, error: "no network", message: "The search model (m) didn't finish downloading: no network. Memory search isn't available; run tag memory download to try again." }),
      status(running(1, 5 * MiB)));
    render(<ModelDownloadNotice api={api} every={5} />);
    expect(await screen.findByText("The search model didn't finish downloading")).toBeTruthy();
    expect(screen.getByText(/Reason: no network/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(calls).toContainEqual(["memory", "download", "--json"]));
    await screen.findByText("Downloading the search model");
  });
});
