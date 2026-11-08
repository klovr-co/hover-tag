// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Tag detail's Works with row: the Tags and apps that can give this Tag tasks,
// and a picker with checkboxes to change them. Nothing is ticked for the owner.
import { useState } from "react";
import type { Bridge } from "../lib/bridge";
import type { TagRow } from "../lib/protocol";
import { SLACK_ID, useWorksWith, type WorksWithCandidate, type WorksWithEntry } from "../lib/worksWith";
import { Avatar, ErrorLine, WorkspaceMark } from "./ui";

type Props = { api: Bridge; row: TagRow; rows: TagRow[]; name: string; running: boolean; say: (text: string) => void };

/** The owner's own Tag for an entry, to show its picture. */
function localTag(entry: WorksWithCandidate, row: TagRow, rows: TagRow[]) {
  if (entry.kind !== "tag") return null;
  return rows.find((r) => r.id === entry.tag)
    ?? rows.find((r) => r.id !== row.id && r.slack_workspace === row.slack_workspace && r.slack_name === entry.name) ?? null;
}

function Who({ entry, row, rows, mine }: { entry: WorksWithCandidate; row: TagRow; rows: TagRow[]; mine?: boolean }) {
  const tag = localTag(entry, row, rows);
  const label = entry.name ?? entry.id;
  return (
    <>
      {tag ? <Avatar row={tag} size={20} badge={false} className="ww-av" /> : <WorkspaceMark label={label} icon={null} />}
      <span className="ww-name" title={entry.id}>{label}</span>
      <span className="meta">{entry.kind === "tag" ? (mine ? "your Tag" : "Tag") : "app"}</span>
    </>
  );
}

export function WorksWith({ api, row, rows, name, running, say }: Props) {
  const works = useWorksWith(api, row.id, { onSaved: (text) => say(`${name} ${text}`) });
  const [picking, setPicking] = useState(false);
  const [ticked, setTicked] = useState<string[]>([]);
  const [find, setFind] = useState("");
  const [pasted, setPasted] = useState("");
  const [extra, setExtra] = useState<WorksWithEntry[]>([]);
  const saved = works.report?.works_with ?? [];

  const open = () => {
    setTicked(saved.map((e) => e.id)); setFind(""); setPasted(""); setExtra([]); setPicking(true);
    if (works.candidates === null) void works.loadCandidates();
  };
  const toggle = (id: string) => setTicked((t) => t.includes(id) ? t.filter((x) => x !== id) : [...t, id]);
  const paste = () => {
    const id = pasted.trim().replace(/^@/, "").toUpperCase();
    if (!SLACK_ID.test(id)) return;
    if (!options.some((o) => o.id === id)) setExtra((x) => [...x, { id, name: null, kind: "app" }]);
    if (!ticked.includes(id)) setTicked((t) => [...t, id]);
    setPasted("");
  };
  // Saved entries first (they may not be in Slack any more), then everyone else.
  const options: WorksWithCandidate[] = [...saved, ...extra];
  for (const c of works.candidates ?? []) if (!options.some((o) => o.id === c.id)) options.push(c);
  const mine = new Set((works.candidates ?? []).filter((c) => c.tag).map((c) => c.id));
  const query = find.trim().toLowerCase();
  const shown = options.filter((o) => !query || (o.name ?? "").toLowerCase().includes(query) || o.id.toLowerCase().includes(query));
  const changed = ticked.length !== saved.length || ticked.some((id) => !saved.some((e) => e.id === id));
  const commit = async () => {
    // Keep the picker's order: saved entries, then new picks in the order shown.
    const ids = options.map((o) => o.id).filter((id) => ticked.includes(id));
    if (await works.save(ids, running)) setPicking(false);
  };

  return (
    <dd className="mdd ww">
      <p className="mcap ww-cap">Tags and apps that can give this Tag tasks. It can also ask the Tags on this list for help.</p>
      {works.report?.error && <ErrorLine>{works.report.error}. Fix it with tag config set.</ErrorLine>}
      {saved.length > 0 && !picking && (
        <ul className="ww-list" aria-label="Works with">
          {saved.map((entry) => <li key={entry.id} className="ww-row"><Who entry={entry} row={row} rows={rows} mine={mine.has(entry.id)} /></li>)}
        </ul>
      )}
      {!picking && works.report && !works.report.error && (
        <div className="ww-acts">
          <button className="p-btn quiet sm" onClick={open}>{saved.length ? "Change" : "Add"}</button>
          {!saved.length && <span className="meta">Nobody yet. Empty until you add someone.</span>}
        </div>
      )}
      {picking && (
        <div className="ww-pick" role="group" aria-label="Choose who this Tag works with">
          <input className="field ww-find" autoFocus placeholder="Search Tags and apps" aria-label="Search Tags and apps"
            value={find} onChange={(e) => setFind(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape") setPicking(false); }} />
          {works.candidates === null && !works.error && <p className="meta ww-loading"><span className="spin" />Looking for Tags and apps…</p>}
          <ul className="ww-options">
            {shown.map((o) => (
              <li key={o.id}>
                <label className="ww-opt">
                  <input type="checkbox" checked={ticked.includes(o.id)} onChange={() => toggle(o.id)} />
                  <Who entry={o} row={row} rows={rows} mine={mine.has(o.id)} />
                </label>
              </li>
            ))}
            {works.candidates !== null && !shown.length && <li className="meta">{query ? "No matches" : "No Tags or apps found"}</li>}
          </ul>
          {works.slackError && <p className="meta ww-note">{works.slackError}</p>}
          <div className="ww-paste">
            <input className="field" placeholder="Not listed? Paste a member ID (U…) or bot ID (B…)" aria-label="Member or bot ID"
              value={pasted} onChange={(e) => setPasted(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") paste(); }} />
            <button className="p-btn quiet sm" disabled={!SLACK_ID.test(pasted.trim().replace(/^@/, "").toUpperCase())} onClick={paste}>Add</button>
          </div>
          <div className="ww-foot">
            <span className="meta">{running ? `Saving restarts ${name}.` : ""}</span>
            <button className="p-btn quiet sm" disabled={works.saving} onClick={() => setPicking(false)}>Cancel</button>
            <button className="p-btn ink sm" disabled={works.saving || !changed} onClick={() => void commit()}>
              {works.saving ? (running ? "Restarting…" : "Saving…") : running ? "Save and restart" : "Save"}
            </button>
          </div>
        </div>
      )}
      {works.error && <ErrorLine>{works.error}</ErrorLine>}
    </dd>
  );
}
