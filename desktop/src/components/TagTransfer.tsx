// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Move a Tag to another computer: Export Tag saves its memory and history, skills,
// threads and settings (never passwords or tokens) to one file; Import adds it here.
// Both run `tag … export|import --json`, the same as the CLI.
import { useState } from "react";
import type { Bridge } from "../lib/bridge";
import { title, type TagRow } from "../lib/protocol";
import { useFresh } from "../lib/motion";
import { exportArgs, exportFileName, importArgs, transferError, type Exported, type Imported } from "../lib/transfer";
import { ErrorLine, Icon, Spinner } from "./ui";
import "./TagTransfer.css";

type Say = (text: string) => void;

export function ExportTag({ api, row, say }: { api: Bridge; row: TagRow; say: Say }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; code: string } | null>(null);
  const [done, setDone] = useState<Exported | null>(null);
  const fresh = useFresh(done?.path ?? null);
  const run = async (withoutHistory = false) => {
    const path = await api.saveFile(exportFileName(row), [{ name: "Tag export", extensions: ["hovertag"] }]).catch(() => null);
    if (!path) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.tag(exportArgs(row.id, path, withoutHistory)).catch(() => null);
      if (!result) { setError({ text: "Couldn't reach Tag. Try again.", code: "" }); return; }
      if (result.code !== 0) { setError(transferError(result.stdout, result.stderr)); return; }
      const exported = (JSON.parse(result.stdout) as { export: Exported }).export;
      setDone(exported);
      say(`Exported ${title(row)}. The file has no passwords or tokens.`);
    } finally { setBusy(false); }
  };
  return (
    <div className="xfer">
      <button className="link" onClick={() => void run()} disabled={busy}>
        {busy ? <Spinner small label="Exporting" /> : <Icon name="disk" size={13} />}Export Tag…
      </button>
      {done && !error && (
        <p className={fresh ? "meta xfer-done fresh" : "meta xfer-done"}>
          <Icon name="check" size={12} /> Saved {done.contents.memory} memory files, {done.contents.skills} skills and {done.contents.threads} threads
          {done.history ? " with history" : ""}.
          {done.redacted.length > 0 && ` Token-shaped text was removed from ${done.redacted.length} file${done.redacted.length === 1 ? "" : "s"}.`}
          <button className="link" onClick={() => void api.open(done.path.replace(/[/\\][^/\\]+$/, ""))}>Show</button>
        </p>
      )}
      {error && <div className="xfer-err">
        <ErrorLine onDismiss={() => setError(null)}>{error.text}</ErrorLine>
        {error.code === "history_has_token" && <button className="link" onClick={() => void run(true)}>Export without history</button>}
      </div>}
    </div>
  );
}

export function ImportTag({ api, say, onImported, className = "sky-btn small" }: {
  api: Bridge; say: Say; onImported: (imported: Imported) => void; className?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async () => {
    const path = await api.pickFile([{ name: "Tag export", extensions: ["hovertag"] }]).catch(() => null);
    if (!path) return;
    setBusy(true);
    setError("");
    try {
      const result = await api.tag(importArgs(path)).catch(() => null);
      if (!result) { setError("Couldn't reach Tag. Try again."); return; }
      if (result.code !== 0) { setError(transferError(result.stdout, result.stderr).text); return; }
      const imported = (JSON.parse(result.stdout) as { imported: Imported }).imported;
      say(`Imported ${imported.name ?? "the Tag"}. Stop it on the old computer, then connect Slack here.`);
      onImported(imported);
    } finally { setBusy(false); }
  };
  return (
    <>
      <button className={className} onClick={() => void run()} disabled={busy} title="Import a Tag from a file">
        {busy ? <Spinner small label="Importing" /> : <Icon name="upload" size={13} />}Import
      </button>
      {error && <div className="xfer-float"><ErrorLine onDismiss={() => setError("")}>{error}</ErrorLine></div>}
    </>
  );
}
