// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// What one Tag knows: its memory and skills as plain documents, each change kept
// in its history with Undo. Everything goes through `tag NAME knowledge … --json`,
// the same commands as the CLI. The Tag remembers only what someone asked it to.
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Bridge } from "../lib/bridge";
import { parseJSON, title, type TagRow } from "../lib/protocol";
import { useFresh, useListMotion, usePresence } from "../lib/motion";
import {
  ACTION_SIGN, actionWords, summaryText, addArgs, addProblem, blocks, day, detailLine, editArgs, fileTitle, findNote, forgetArgs, historyArgs, keyFrom,
  knowledgeError, listArgs, readArgs, showArgs, size, threadLink, undoArgs, who, withEntry, APP_EDIT_LIMIT,
  type AddForm, type Change, type ChangeDetail, type Entry, type History, type Inline, type KnowledgeFile, type KnowledgeList, type Node,
} from "../lib/knowledge";
import { ErrorLine, fitText, Icon, Primary, Quiet, Secondary, Spinner } from "./ui";
import "./Knowledge.css";

type Say = (text: string) => void;
export type OpenThread = (tag: string, channel: string, ts: string) => void;

interface Props {
  api: Bridge; row: TagRow; say: Say;
  /** Opens a `hover-tag://…/thread/…` source in the Threads screen; without it the link goes to the OS. */
  openThread?: OpenThread;
}

export function Knowledge({ api, row, say, openThread }: Props) {
  const [list, setList] = useState<KnowledgeList | null>(null);
  const [loadError, setLoadError] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [file, setFile] = useState<KnowledgeFile | null>(null);
  const [error, setError] = useState("");
  const [mode, setMode] = useState<"visual" | "markdown">("visual");
  const [history, setHistory] = useState(false);
  const [adding, setAdding] = useState(false);
  const [changed, setChanged] = useState(0);
  const request = useRef(0);

  const run = useCallback(async <T,>(args: string[]): Promise<{ ok: true; value: T } | { ok: false; error: string; code: string }> => {
    const result = await api.tag(args).catch(() => null);
    if (!result) return { ok: false, error: "Couldn't reach Tag. Try again.", code: "unreachable" };
    if (result.code !== 0) {
      return { ok: false, ...knowledgeError(result.stdout, result.stderr) };
    }
    return { ok: true, value: parseJSON<T>(result.stdout) };
  }, [api]);

  const loadList = useCallback(async () => {
    const current = ++request.current;
    const result = await run<KnowledgeList>(listArgs(row.id));
    if (current !== request.current) return;
    if (!result.ok) { setLoadError(result.error); return; }
    setLoadError("");
    setList(result.value);
    setSelected((s) => s ?? firstPath(result.value));
  }, [run, row.id]);

  const loadFile = useCallback(async (path: string) => {
    const result = await run<{ file: KnowledgeFile }>(readArgs(row.id, path));
    if (!result.ok) { setError(result.error); setFile(null); return null; }
    setError("");
    setFile(result.value.file);
    return result.value.file;
  }, [run, row.id]);

  useEffect(() => {
    void loadList();
    const timer = setInterval(() => void loadList(), 30_000);
    return () => { request.current++; clearInterval(timer); };
  }, [loadList]);
  useEffect(() => { if (selected) void loadFile(selected); }, [selected, loadFile]);

  /** After any change: reload the file, the tree and, if open, the history. */
  const refresh = async (path = selected) => {
    setChanged((n) => n + 1);
    await Promise.all([loadList(), path ? loadFile(path) : null]);
  };

  const pick = (path: string) => { setSelected(path); setError(""); setMode("visual"); };
  const openSource = (link: string | null) => {
    const parts = threadLink(link);
    if (!parts) return;
    if (openThread) openThread(parts.tag, parts.channel, parts.ts); else void api.open(link!);
  };

  if (!list) {
    return loadError ? <ErrorLine>{loadError}</ErrorLine> : <p className="meta" style={{ margin: 0 }}>Loading what {title(row)} knows…</p>;
  }
  return (
    <div className="kn">
      <Tree list={list} selected={selected} pick={pick} add={() => setAdding(true)} />
      <div className="kn-doc">
        {loadError && <ErrorLine>{loadError}</ErrorLine>}
        {file ? (
          <Document key={file.path} row={row} file={file} list={list} mode={mode} setMode={setMode} run={run}
            error={error} setError={setError} refresh={refresh} say={say} openSource={openSource} pick={pick}
            history={history} toggleHistory={() => setHistory((h) => !h)} />
        ) : error ? <ErrorLine>{error}</ErrorLine> : <p className="meta">Pick something on the left.</p>}
      </div>
      <HistoryPanel open={history} row={row} channels={list.channels} path={selected} changed={changed} run={run}
        close={() => setHistory(false)} openSource={openSource}
        undone={async (message) => { say(message); await refresh(); }} />
      <AddDialog open={adding} row={row} list={list} selected={file} run={run} close={() => setAdding(false)}
        added={async (path, message) => { setAdding(false); say(message); setSelected(path); await refresh(path); }} />
    </div>
  );
}

function firstPath(list: KnowledgeList) {
  const channel = list.channels.find((c) => c.core);
  return list.memory.core?.path ?? channel?.core?.path ?? list.memory.notes[0]?.path ?? list.skills[0]?.path ?? "memory/core.md";
}

// ---- The tree on the left ----------------------------------------------------------

function Tree({ list, selected, pick, add }: { list: KnowledgeList; selected: string | null; pick: (path: string) => void; add: () => void }) {
  const [open, setOpen] = useState<Record<string, boolean>>({ memory: true, channels: true, skills: false });
  const toggle = (key: string) => setOpen((o) => ({ ...o, [key]: !o[key] }));
  const withMemory = list.channels.filter((c) => c.core || c.notes.length);
  const leaf = (path: string, label: ReactNode, sizeText: string, depth: number, extra?: string) => (
    <button key={path} className={`kn-leaf d${depth}${selected === path ? " sel" : ""}`} aria-current={selected === path ? "page" : undefined}
      onClick={() => pick(path)}>
      <Icon name="file" size={13} /><span className="kn-label">{label}</span>
      {extra && <span className="kn-ds">{extra}</span>}<span className="kn-sz">{sizeText}</span>
    </button>
  );
  const folder = (key: string, label: ReactNode, depth: number, hint?: string, count?: number) => (
    <button key={key} className={`kn-folder d${depth}`} aria-expanded={!!open[key]} onClick={() => toggle(key)}>
      <span className={open[key] ? "chev turn" : "chev"}><Icon name="right" size={11} /></span>
      <span className="kn-label">{label}</span>{hint && <span className="kn-ds">{hint}</span>}
      {count !== undefined && <span className="kn-sz">{count}</span>}
    </button>
  );
  const notes = (key: string, items: Node[], depth: number) => items.length ? <>
    {folder(key, "notes", depth, undefined, items.length)}
    {open[key] && <div className="kn-kids">{items.map((n) => leaf(n.path, n.key, size(n.size), depth + 1))}</div>}
  </> : null;
  return (
    <nav className="kn-tree" aria-label="Knowledge">
      <div className="kn-tree-scroll">
        {folder("memory", "memory", 0, "What I remember")}
        {open.memory && <div className="kn-kids">
          {leaf(list.memory.core?.path ?? "memory/core.md", "all channels", list.memory.core ? size(list.memory.core.size) : "", 1, "Always loaded")}
          {notes("notes:all", list.memory.notes, 1)}
          {folder("channels", "channels", 1, undefined, withMemory.length)}
          {open.channels && <div className="kn-kids">
            {withMemory.map((c) => {
              const label = c.name ? `#${c.name}` : c.id;
              if (!c.notes.length) return leaf(c.core!.path, label, size(c.core!.size), 2);
              return <div key={c.id}>
                {c.core ? leaf(c.core.path, label, size(c.core.size), 2) : <div className="kn-leaf d2 none">{label}</div>}
                {notes(`notes:${c.id}`, c.notes, 3)}
              </div>;
            })}
            {!withMemory.length && <p className="kn-empty">Nothing saved for one channel yet.</p>}
          </div>}
        </div>}
        {folder("skills", "skills", 0, "How I do things", list.skills.length)}
        {open.skills && <div className="kn-kids">
          {list.skills.map((s) => leaf(s.path, s.name, size(s.size), 1, s.backend === "codex" ? "Codex" : "Claude"))}
          {!list.skills.length && <p className="kn-empty">No skills in this Tag's folder.</p>}
        </div>}
      </div>
      <button className="kn-add-btn" onClick={add}><span className="gi"><Icon name="plus" size={12} /></span>Add to knowledge</button>
    </nav>
  );
}

// ---- The document in the middle ---------------------------------------------------------

type Run = <T>(args: string[]) => Promise<{ ok: true; value: T } | { ok: false; error: string; code: string }>;

function Document({ row, file, list, mode, setMode, run, error, setError, refresh, say, openSource, pick, history, toggleHistory }: {
  row: TagRow; file: KnowledgeFile; list: KnowledgeList; mode: "visual" | "markdown"; setMode: (m: "visual" | "markdown") => void;
  run: Run; error: string; setError: (e: string) => void; refresh: (path?: string | null) => Promise<void>; say: Say;
  openSource: (link: string | null) => void; pick: (path: string) => void; history: boolean; toggleHistory: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [shake, setShake] = useState(0);
  const tooBig = file.kind === "skill" && file.body.length > APP_EDIT_LIMIT;
  /** Save a whole new body; true when it saved. */
  const save = async (body: string, done: string) => {
    setBusy(true);
    setError("");
    try {
      const result = await run<{ change: Change | null }>(editArgs(row.id, file.path, file.version, body));
      if (!result.ok) { setError(result.error); setShake((n) => n + 1); return false; }
      say(done);
      await refresh(file.path);
      return true;
    } finally { setBusy(false); }
  };
  const forget = async (key?: string) => {
    setBusy(true);
    try {
      const result = await run(forgetArgs(row.id, file.path, key));
      if (!result.ok) { setError(result.error); setShake((n) => n + 1); return; }
      say(`${title(row)} forgot ${key ?? file.key}. You can undo this in History.`);
      await refresh(file.kind === "note" ? null : file.path);
    } finally { setBusy(false); }
  };
  return (
    <div className="kn-page">
      <div className="kn-head">
        <div className="kn-title">
          <b>{fileTitle(file)}</b>
          <span className="kn-path">{file.path}</span>
        </div>
        <div className="segc" role="radiogroup" aria-label="View">
          <button role="radio" aria-checked={mode === "visual"} onClick={() => setMode("visual")}>Visual</button>
          <button role="radio" aria-checked={mode === "markdown"} onClick={() => setMode("markdown")}>Markdown</button>
        </div>
        <button className={history ? "kn-hbtn on" : "kn-hbtn"} aria-pressed={history} onClick={toggleHistory}>
          <Icon name="clock" size={13} />History
        </button>
      </div>
      <p className="kn-details">{detailLine(file)}</p>
      {error && <div key={shake} className={shake ? "kn-shake" : undefined}><ErrorLine onDismiss={() => setError("")}>{error}</ErrorLine></div>}
      {mode === "markdown"
        ? <MarkdownEditor key={file.version} file={file} busy={busy} readOnly={tooBig}
            save={(body) => save(body, file.kind === "skill" ? `Saved ${file.name}` : "Saved. The Tag uses it from its next reply.")} />
        : file.kind === "core"
          ? <CoreEntries file={file} list={list} busy={busy} openSource={openSource} pick={pick}
              save={(key, text) => save(withEntry(file.entries ?? [], key, text), `Saved ${key}. The Tag uses it from its next reply.`)}
              forget={(key) => forget(key)} />
          : <Single file={file} list={list} busy={busy} readOnly={tooBig} openSource={openSource} pick={pick}
              save={(body) => save(body, file.kind === "skill" ? `Saved ${file.name}` : `Saved ${file.key}. The Tag uses it from its next reply.`)}
              forget={file.kind === "note" ? () => forget() : undefined} />}
      {tooBig && <p className="meta">This skill is too long to edit here. Open it in the Tag's folder to change it.</p>}
    </div>
  );
}

function CoreEntries({ file, list, busy, save, forget, openSource, pick }: {
  file: KnowledgeFile; list: KnowledgeList; busy: boolean; save: (key: string, text: string) => Promise<boolean>; forget: (key: string) => void;
  openSource: (link: string | null) => void; pick: (path: string) => void;
}) {
  const entries = useListMotion(file.entries ?? [], (e) => e.key);
  const [editing, setEditing] = useState<string | null>(null);
  if (!entries.length) {
    return <p className="kn-empty big">Nothing is always loaded here yet. Ask the Tag to remember something in Slack, or use Add to knowledge.</p>;
  }
  return (
    <div className="kn-entries">
      {entries.map(({ item, state }) => (
        <EntryCard key={item.key} entry={item} state={state} file={file} list={list} busy={busy} editing={editing === item.key}
          edit={() => setEditing(item.key)} cancel={() => setEditing(null)} openSource={openSource} pick={pick}
          save={async (text) => { if (await save(item.key, text)) setEditing(null); }} forget={() => forget(item.key)} />
      ))}
    </div>
  );
}

function EntryCard({ entry, state, file, list, busy, editing, edit, cancel, save, forget, openSource, pick }: {
  entry: Entry; state: "stay" | "enter" | "leave"; file: KnowledgeFile; list: KnowledgeList; busy: boolean; editing: boolean;
  edit: () => void; cancel: () => void; save: (text: string) => void; forget: () => void;
  openSource: (link: string | null) => void; pick: (path: string) => void;
}) {
  const fresh = useFresh(entry.version);
  const [draft, setDraft] = useState(entry.text);
  useEffect(() => { if (editing) setDraft(entry.text); }, [editing, entry.text]);
  const cls = ["kn-entry", state === "enter" ? "entering" : state === "leave" ? "leaving" : "", fresh ? "fresh" : ""].filter(Boolean).join(" ");
  return (
    <article className={cls} tabIndex={-1}>
      <div className="kn-eh">
        <code>{entry.key}</code>
        <span className="kn-when">{entry.updated ? day(entry.updated) : ""}</span>
        <span className="sp" />
        {!editing && <span className="kn-acts">
          <button className="kn-act" onClick={edit} disabled={busy}>Edit</button>
          <button className="kn-act" onClick={forget} disabled={busy}>Forget</button>
        </span>}
      </div>
      {editing ? (
        <Editing draft={draft} setDraft={setDraft} busy={busy} cancel={cancel} save={() => save(draft)} changed={draft !== entry.text} />
      ) : <Markdown text={entry.text} list={list} channel={file.channel} pick={pick} />}
      <Source details={entry} openSource={openSource} />
    </article>
  );
}

/** Where an entry came from; shown while its card is hovered or focused. */
function Source({ details, openSource }: { details: { written_by: string | null; from_thread: string | null; updated: string | null }; openSource: (link: string | null) => void }) {
  if (!details.written_by && !details.from_thread) return null;
  const thread = threadLink(details.from_thread);
  return (
    <div className="kn-src" role="note">
      Saved by {who(details.written_by)}{details.updated ? ` · ${day(details.updated, true)}` : ""}
      {thread && <button className="kn-src-link" onClick={() => openSource(details.from_thread)}>from a Slack thread<Icon name="right" size={11} /></button>}
    </div>
  );
}

function Single({ file, list, busy, readOnly, save, forget, openSource, pick }: {
  file: KnowledgeFile; list: KnowledgeList; busy: boolean; readOnly: boolean; save: (body: string) => Promise<boolean>; forget?: () => void;
  openSource: (link: string | null) => void; pick: (path: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(file.body);
  return (
    <article className="kn-entry single">
      <div className="kn-eh">
        <span className="sp" />
        {!editing && !readOnly && <span className="kn-acts shown">
          <button className="kn-act" onClick={() => { setDraft(file.body); setEditing(true); }} disabled={busy}>Edit</button>
          {forget && <button className="kn-act" onClick={forget} disabled={busy}>Forget</button>}
        </span>}
      </div>
      {editing
        ? <Editing draft={draft} setDraft={setDraft} busy={busy} cancel={() => setEditing(false)} changed={draft !== file.body}
            save={async () => { if (await save(draft)) setEditing(false); }} />
        : <Markdown text={file.body} list={list} channel={file.channel} pick={pick} />}
      <Source details={file} openSource={openSource} />
    </article>
  );
}

function Editing({ draft, setDraft, busy, cancel, save, changed }: {
  draft: string; setDraft: (text: string) => void; busy: boolean; cancel: () => void; save: () => void; changed: boolean;
}) {
  return (
    <div className="kn-editing">
      <textarea className="field kn-text" ref={fitText} value={draft} autoFocus aria-label="Text"
        onChange={(e) => { setDraft(e.target.value); fitText(e.target); }}
        onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); if (changed) save(); } if (e.key === "Escape") cancel(); }} />
      <div className="kn-bar">
        <Quiet title="Cancel" small onClick={cancel} disabled={busy} />
        <Primary title={busy ? "Saving…" : "Save"} small onClick={save} disabled={busy || !changed || !draft.trim()} />
      </div>
    </div>
  );
}

function MarkdownEditor({ file, busy, readOnly, save }: { file: KnowledgeFile; busy: boolean; readOnly: boolean; save: (body: string) => Promise<boolean> }) {
  const [draft, setDraft] = useState(file.body);
  const changed = draft !== file.body;
  return (
    <div className="kn-md">
      <textarea className="field kn-text mono" value={draft} readOnly={readOnly} spellCheck={false} aria-label="Markdown"
        placeholder={file.kind === "core" ? "## report-deadline\nThe weekly report is due Friday." : ""}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); if (changed) void save(draft); } }} />
      {file.kind === "core" && <p className="meta kn-hint">Each entry starts with a <code>## key</code> line. Removing an entry forgets it.</p>}
      {changed && <div className="kn-bar savebar">
        <Quiet title="Revert" small onClick={() => setDraft(file.body)} disabled={busy} />
        <Primary title={busy ? "Saving…" : "Save"} small onClick={() => void save(draft)} disabled={busy} />
      </div>}
    </div>
  );
}

// ---- Markdown, read-only ---------------------------------------------------------------

function Markdown({ text, list, channel, pick }: { text: string; list: KnowledgeList; channel?: string | null; pick: (path: string) => void }) {
  const parsed = useMemo(() => blocks(text), [text]);
  const span = (items: Inline[]) => items.map((part, n) => {
    if (part.t === "b") return <b key={n}>{part.v}</b>;
    if (part.t === "i") return <i key={n}>{part.v}</i>;
    if (part.t === "code") return <code key={n}>{part.v}</code>;
    if (part.t === "link") return <a key={n} href={part.href} onClick={(e) => { e.preventDefault(); }} title={part.href}>{part.v}</a>;
    if (part.t === "key") {
      const note = findNote(list, part.v, channel);
      return note
        ? <button key={n} className="kn-key" onClick={() => pick(note.path)}>{part.v}</button>
        : <span key={n} className="kn-key missing" title="Nothing is saved under this name">{part.v}</span>;
    }
    return <span key={n}>{part.v}</span>;
  });
  return (
    <div className="kn-prose selectable">
      {parsed.map((block, n) => block.t === "p" ? <p key={n}>{span(block.v)}</p>
        : block.t === "h" ? <p key={n} className={`kn-h h${Math.min(block.level, 3)}`}>{span(block.v)}</p>
          : block.t === "ul" ? <ul key={n}>{block.items.map((item, i) => <li key={i}>{span(item)}</li>)}</ul>
            : <ol key={n}>{block.items.map((item, i) => <li key={i}>{span(item)}</li>)}</ol>)}
    </div>
  );
}

// ---- History on the right ----------------------------------------------------------------

function HistoryPanel({ open, row, channels, path, changed, run, close, openSource, undone }: {
  open: boolean; row: TagRow; channels: KnowledgeList["channels"]; path: string | null; changed: number; run: Run; close: () => void;
  openSource: (link: string | null) => void; undone: (message: string) => Promise<void>;
}) {
  const { shown, leaving } = usePresence(open ? true : null, 220);
  const [scope, setScope] = useState<"file" | "all">("file");
  const [data, setData] = useState<History | null>(null);
  const [error, setError] = useState("");
  const [picked, setPicked] = useState<string | null>(null);
  const [detail, setDetail] = useState<ChangeDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const target = scope === "file" ? path : null;
  useEffect(() => {
    if (!open) return;
    let live = true;
    void run<History>(historyArgs(row.id, target)).then((result) => {
      if (!live) return;
      if (!result.ok) { setError(result.error); return; }
      setError("");
      setData(result.value);
    });
    return () => { live = false; };
  }, [open, run, row.id, target, changed]);
  useEffect(() => {
    if (!picked) { setDetail(null); return; }
    let live = true;
    void run<ChangeDetail>(showArgs(row.id, picked)).then((result) => { if (live && result.ok) setDetail(result.value); });
    return () => { live = false; };
  }, [picked, run, row.id, changed]);
  const rows = useListMotion(data?.changes ?? [], (c) => c.id);
  if (!shown) return null;
  const undo = async (change: Change) => {
    setBusy(true);
    try {
      const result = await run<{ change: Change }>(undoArgs(row.id, change.id));
      if (!result.ok) { setError(result.error); return; }
      setPicked(null);
      await undone(`Undone: ${summaryText(change.summary, channels)}`);
    } finally { setBusy(false); }
  };
  return (
    <aside className={leaving ? "kn-history leaving" : "kn-history"} aria-label="History">
      <div className="kn-hh">
        <b>History</b>
        <div className="segc" role="radiogroup" aria-label="Show changes to">
          <button role="radio" aria-checked={scope === "file"} onClick={() => setScope("file")}>This file</button>
          <button role="radio" aria-checked={scope === "all"} onClick={() => setScope("all")}>All</button>
        </div>
        <span className="sp" />
        <button className="kn-x" aria-label="Close history" onClick={close}><Icon name="close" size={12} /></button>
      </div>
      {error && <ErrorLine onDismiss={() => setError("")}>{error}</ErrorLine>}
      {!data ? <Spinner small label="Loading history" /> : !data.changes.length ? <p className="kn-empty">No changes yet.</p> : (
        <ol className="kn-changes">
          {rows.map(({ item, state }) => (
            <li key={item.id} className={["kn-chg", picked === item.id ? "sel" : "", state === "enter" ? "entering" : state === "leave" ? "leaving" : ""].filter(Boolean).join(" ")}>
              <button className="kn-chg-row" aria-expanded={picked === item.id} onClick={() => setPicked(picked === item.id ? null : item.id)}>
                <span className={`kn-sign ${ACTION_SIGN[item.action] === "+" ? "a" : ACTION_SIGN[item.action] === "−" ? "r" : "c"}`}>{ACTION_SIGN[item.action] ?? "~"}</span>
                <span className="kn-chg-txt"><span className="kn-chg-sum">{summaryText(item.summary, channels)}</span>
                  <small>{actionWords(item)}{item.undone_by ? " · undone" : ""}</small></span>
              </button>
              {picked === item.id && (
                <div className="kn-chg-detail">
                  {!detail || detail.change.id !== item.id ? <Spinner small label="Loading change" /> : <>
                    {detail.files.map((f) => <Diff key={f.path} file={f} />)}
                    {!detail.files.length && <p className="meta">No text changed.</p>}
                    <div className="kn-bar left">
                      {item.undoable && !item.undone_by && <Secondary title={busy ? "Undoing…" : "Undo this change"} small icon="restart" disabled={busy} onClick={() => void undo(item)} />}
                      {threadLink(item.from_thread) && <Quiet title="Open thread" small onClick={() => openSource(item.from_thread)} />}
                    </div>
                  </>}
                </div>
              )}
            </li>
          ))}
        </ol>
      )}
    </aside>
  );
}

function Diff({ file }: { file: ChangeDetail["files"][number] }) {
  return (
    <div className="kn-diff selectable">
      <div className="kn-diff-path">{file.path}{file.status !== "changed" ? ` · ${file.status}` : ""}</div>
      {file.diff.map((line, n) => (
        <div key={n} className={line.op === "+" ? "dp" : line.op === "-" ? "dm" : line.op === "…" ? "gap" : ""}>
          {line.op === "…" ? "…" : `${line.op} ${line.text}`}
        </div>
      ))}
    </div>
  );
}

// ---- Add to knowledge ----------------------------------------------------------------

function AddDialog({ open, row, list, selected, run, close, added }: {
  open: boolean; row: TagRow; list: KnowledgeList; selected: KnowledgeFile | null; run: Run; close: () => void;
  added: (path: string, message: string) => Promise<void>;
}) {
  const { shown, leaving } = usePresence(open ? true : null, 180);
  const blank = (): AddForm => ({ key: "", text: "", kind: "core", channel: selected?.scope === "channel" ? selected.channel ?? "" : "" });
  const [form, setForm] = useState<AddForm>(blank);
  const [named, setNamed] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) { setForm(blank()); setNamed(false); setError(""); } }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!shown) return null;
  const channels = (row.channels ?? []).map((c) => ({ id: c.id, name: c.name }));
  for (const c of list.channels) if (!channels.some((x) => x.id === c.id)) channels.push({ id: c.id, name: c.name });
  const problem = addProblem(form, list.limits);
  const submit = async () => {
    if (problem) { setError(problem); return; }
    setBusy(true);
    try {
      const result = await run<{ file: KnowledgeFile | null }>(addArgs(row.id, form));
      if (!result.ok) { setError(result.error); return; }
      const path = result.value.file?.path ?? (form.channel ? `memory/channels/${form.channel}/core.md` : "memory/core.md");
      await added(path, `${title(row)} will remember ${form.key.trim()} from its next reply.`);
    } finally { setBusy(false); }
  };
  return (
    <div className={leaving ? "kn-scrim leaving" : "kn-scrim"} onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}>
      <div className="kn-dialog" role="dialog" aria-modal="true" aria-labelledby="kn-add-title"
        onKeyDown={(e) => { if (e.key === "Escape") close(); }}>
        <b id="kn-add-title">Add to {title(row)}'s knowledge</b>
        <p className="meta">The Tag keeps only what you add here or what someone asks it to remember.</p>
        <label className="kn-field">What to remember
          <textarea className="field kn-text" ref={fitText} autoFocus value={form.text} placeholder="The weekly report is due on Friday."
            onChange={(e) => { const text = e.target.value; setForm((f) => ({ ...f, text, key: named ? f.key : keyFrom(text) })); fitText(e.target); }} />
        </label>
        <label className="kn-field">Name
          <input className="field kn-input" value={form.key} placeholder="report-deadline" spellCheck={false}
            onChange={(e) => { setNamed(true); setForm((f) => ({ ...f, key: e.target.value })); }} />
        </label>
        <div className="kn-row2">
          <label className="kn-field">Where
            <select className="field kn-input" value={form.channel} onChange={(e) => setForm((f) => ({ ...f, channel: e.target.value }))}>
              <option value="">All channels</option>
              {channels.map((c) => <option key={c.id} value={c.id}>{c.name ? `#${c.name}` : c.id}</option>)}
            </select>
          </label>
          <div className="kn-field">How
            <div className="segc" role="radiogroup" aria-label="How">
              <button role="radio" aria-checked={form.kind === "core"} onClick={() => setForm((f) => ({ ...f, kind: "core" }))}>Always loaded</button>
              <button role="radio" aria-checked={form.kind === "note"} onClick={() => setForm((f) => ({ ...f, kind: "note" }))}>Note</button>
            </div>
          </div>
        </div>
        {error && <ErrorLine onDismiss={() => setError("")}>{error}</ErrorLine>}
        <div className="kn-bar">
          <Quiet title="Cancel" small onClick={close} disabled={busy} />
          <Primary title={busy ? "Adding…" : "Add"} small onClick={() => void submit()} disabled={busy || !form.text.trim()} />
        </div>
      </div>
    </div>
  );
}
