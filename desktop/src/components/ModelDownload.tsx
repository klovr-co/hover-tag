// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// The built-in search model downloads in the background after install. This tells the person how far
// it is and that memory search waits for it, and offers to try again when it stopped.
import { useCallback, useEffect, useRef, useState } from "react";
import type { Bridge } from "../lib/bridge";
import { parseMemory, type ModelDownload } from "../lib/memory";
import { Icon } from "./ui";

const mb = (bytes: number) => Math.round(bytes / 1048576);

export function ModelDownloadNotice({ api, every = 3000 }: { api: Bridge; every?: number }) {
  const [download, setDownload] = useState<ModelDownload | null>(null);
  const [finished, setFinished] = useState(false);
  const was = useRef(false);
  const live = useRef(true);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const read = useCallback(async () => {
    let next: ModelDownload | null = null;
    try {
      const result = await api.tag(["memory", "status", "--json"]);
      if (result.code === 0) next = parseMemory(result.stdout).embedding?.download ?? null;
    } catch { /* memory not reachable: say nothing */ }
    if (!live.current) return;
    if (next?.state === "ready" && was.current) setFinished(true);
    was.current = next?.state === "downloading";
    setDownload(next);
    // Asking stops with the download: the next ask is the person's retry or the next launch.
    if (next?.state === "downloading") timer.current = setTimeout(() => void read(), every);
  }, [api, every]);

  useEffect(() => {
    live.current = true;
    void read();
    return () => { live.current = false; clearTimeout(timer.current); };
  }, [read]);

  const retry = async () => {
    await api.tag(["memory", "download", "--json"]).catch(() => null);
    clearTimeout(timer.current);
    await read();
  };

  if (finished) {
    return (
      <div className="notice ok" role="status">
        <span className="ic"><Icon name="check" size={16} /></span>
        <div style={{ flex: 1 }}><div className="t">Memory search is ready</div>
          <div className="d">The search model is downloaded.</div></div>
      </div>
    );
  }
  if (download?.state === "downloading") {
    const sized = download.total > 0 ? ` · ${mb(download.done)} of ${mb(download.total)} MB` : "";
    return (
      <div className="notice info" role="status">
        <span className="ic"><Icon name="disk" size={16} /></span>
        <div style={{ flex: 1 }}>
          <div className="t">Downloading the search model</div>
          <div className="d">{download.model.split("/").pop() || "Built-in model"}{sized}. Search waits for it; Tags keep working.</div>
          <div className="dl-bar" role="progressbar" aria-label="Search model download" aria-valuemin={0} aria-valuemax={100} aria-valuenow={download.percent}>
            <span style={{ width: `${download.percent}%` }} />
          </div>
        </div>
      </div>
    );
  }
  if (download?.state === "failed") {
    return (
      <div className="notice" role="alert">
        <span className="ic"><Icon name="warn" size={16} /></span>
        <div style={{ flex: 1 }}>
          <div className="t">Search model download stopped</div>
          <div className="d">Search is off until it finishes. Tags keep working.</div>
          {download.error && <div className="d selectable">Reason: {download.error}. Try again, or run <code>tag memory download</code>.</div>}
        </div>
        <div className="act"><button className="p-btn ink sm" onClick={() => void retry()}>Try again</button></div>
      </div>
    );
  }
  return null;
}
