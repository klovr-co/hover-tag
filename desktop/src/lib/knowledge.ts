// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// What one Tag knows: its memory files and skills, with their history.
// The app only reads and changes them through `tag NAME knowledge … --json`.
import listExample from "../../../protocol/examples/knowledge-list.json";
import readExample from "../../../protocol/examples/knowledge-read.json";
import historyExample from "../../../protocol/examples/knowledge-history.json";
import showExample from "../../../protocol/examples/knowledge-show.json";
import { parseJSON } from "./protocol";

export type Kind = "core" | "note" | "skill";

export interface Details { written_by: string | null; from_thread: string | null; updated: string | null }

export interface Node extends Details {
  path: string; kind: Kind; size: number; limit?: number;
  /** Core files: how many entries. */
  entries?: number;
  key?: string; name?: string; backend?: string; summary?: string;
}

export interface ChannelNode { id: string; name: string | null; core: Node | null; notes: Node[] }

export interface KnowledgeList {
  memory: { core: Node | null; notes: Node[] };
  channels: ChannelNode[];
  skills: Node[];
  limits: { core: number; note: number; notes: number };
  history: { available: boolean };
}

export interface Entry extends Details { key: string; text: string; version: string; links: string[] }

export interface KnowledgeFile extends Details {
  path: string; kind: Kind; version: string; body: string; size: number; limit: number;
  scope?: "all" | "channel"; channel?: string | null; channel_name?: string | null;
  entries?: Entry[]; key?: string; links?: string[]; name?: string; backend?: string; summary?: string;
}

export interface Change {
  id: string; at: string; action: string; summary: string;
  written_by: string | null; via: string | null; from_thread: string | null;
  entries: string[]; undo_of: string | null; undoable: boolean; undone_by: string | null;
}

export interface DiffLine { op: " " | "+" | "-" | "…"; text: string }
export interface ShownFile { path: string; status: "added" | "changed" | "removed"; before: string; after: string; diff: DiffLine[] }
export interface ChangeDetail { change: Change; files: ShownFile[] }
export interface History { changes: Change[]; has_more: boolean }

/** Skills longer than this are edited in their folder: the text travels on the command line. */
export const APP_EDIT_LIMIT = 20_000;

/** The CLI's own message and code from a failed --json command. */
export function knowledgeError(stdout: string, stderr: string): { error: string; code: string } {
  try {
    const parsed = parseJSON<{ error?: string; code?: string }>(stdout);
    if (parsed.error) return { error: parsed.error, code: parsed.code ?? "" };
  } catch { /* fall through to stderr */ }
  return { error: stderr.trim().split("\n").pop()?.replace(/^Error:\s*/, "") || "Tag couldn't change its knowledge.", code: "" };
}

// ---- Commands -----------------------------------------------------------------

export const listArgs = (tag: string) => [tag, "knowledge", "list", "--json"];
export const readArgs = (tag: string, path: string) => [tag, "knowledge", "read", path, "--json"];
export const editArgs = (tag: string, path: string, base: string, body: string) =>
  [tag, "knowledge", "edit", path, `--body=${body}`, "--base", base, "--via", "tag-app", "--json"];
export const historyArgs = (tag: string, path: string | null, before?: string) =>
  [tag, "knowledge", "history", ...(path ? [path] : []), ...(before ? ["--before", before] : []), "--limit", "30", "--json"];
export const showArgs = (tag: string, id: string) => [tag, "knowledge", "show", id, "--json"];
export const undoArgs = (tag: string, id: string) => [tag, "knowledge", "undo", id, "--via", "tag-app", "--json"];
export const forgetArgs = (tag: string, path: string, key?: string) =>
  [tag, "knowledge", "forget", path, ...(key ? ["--key", key] : []), "--via", "tag-app", "--json"];

export interface AddForm { key: string; text: string; kind: "core" | "note"; channel: string }

/** `channel` is a channel ID, or "" for all channels. */
// `--name=value` keeps text that starts with a dash from reading as an option.
export const addArgs = (tag: string, form: AddForm) => [tag, "knowledge", "add", `--key=${form.key.trim()}`, `--text=${form.text.trim()}`,
  "--kind", form.kind, ...(form.channel ? ["--channel", form.channel] : ["--all-channels"]), "--via", "tag-app", "--json"];

/** A key from what someone typed: "Report deadline!" → "report-deadline". */
export function keyFrom(text: string) {
  const words = text.toLowerCase().normalize("NFKD").replace(/[^a-z0-9\s-]/g, "").trim().split(/[\s-]+/).filter(Boolean);
  return words.slice(0, 4).join("-").slice(0, 64).replace(/-+$/, "");
}

export function addProblem(form: AddForm, limits: KnowledgeList["limits"]) {
  if (!/^[a-z0-9][a-z0-9-]{0,63}$/.test(form.key.trim())) return "Give it a short name: lowercase letters, numbers and dashes, such as report-deadline.";
  if (!form.text.trim()) return "Write what the Tag should remember.";
  if (form.kind === "note" && form.text.trim().length > limits.note) return `A note holds up to ${limits.note.toLocaleString("en-GB")} characters.`;
  return "";
}

// ---- core.md ---------------------------------------------------------------------

/** One `## key` section per entry, the way Tag writes core.md. */
export function coreBody(entries: { key: string; text: string }[]) {
  return entries.map((e) => `## ${e.key}\n${e.text.split("\n").map((l) => /^(\\*)## /.test(l) ? "\\" + l : l).join("\n")}\n`).join("\n");
}

/** The body with one entry's text replaced (or removed with `text` null). */
export function withEntry(entries: Entry[], key: string, text: string | null) {
  return coreBody(entries.flatMap((e) => e.key !== key ? [e] : text === null ? [] : [{ key, text }]));
}

// ---- Words for people ------------------------------------------------------------

export function who(id: string | null) {
  if (!id) return "someone";
  if (id === "local-operator") return "you, in Tag.app";
  if (id === "tag") return "Tag";
  return /^[UW][A-Z0-9]+$/.test(id) ? `${id} in Slack` : id;
}

/** "22 Oct" this year, "22 Oct 2025" otherwise, "22 Oct, 09:03" with `time`. */
export function day(iso: string | null, time = false, now = new Date()) {
  if (!iso) return "";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  return at.toLocaleString("en-GB", { day: "numeric", month: "short", ...(at.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}),
    ...(time ? { hour: "2-digit", minute: "2-digit" } : {}) });
}

export const size = (n: number) => n >= 1000 ? `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k` : `${n}`;

/** `hover-tag://tag/ID/thread/CHANNEL/TS` → its parts, or null. */
export function threadLink(link: string | null) {
  const match = /^hover-tag:\/\/tag\/([A-Za-z0-9._-]+)\/thread\/([A-Z0-9]+)\/(\d+\.\d+)$/.exec(link ?? "");
  return match ? { tag: match[1], channel: match[2], ts: match[3] } : null;
}

export function whereLabel(file: Pick<KnowledgeFile, "scope" | "channel" | "channel_name">) {
  if (file.scope === "all") return "All channels";
  return file.channel_name ? `#${file.channel_name}` : (file.channel ?? "");
}

export function fileTitle(file: KnowledgeFile) {
  if (file.kind === "skill") return file.name ?? file.path;
  if (file.kind === "note") return file.key ?? file.path;
  return `${whereLabel(file)}: always remember`;
}

/** The one grey line under a file's title. */
export function detailLine(file: KnowledgeFile) {
  const parts: string[] = [];
  if (file.kind === "core") parts.push(`${whereLabel(file)} · always loaded`, `${size(file.size)} / ${size(file.limit)}`);
  if (file.kind === "note") parts.push(`${whereLabel(file)} · note, read when needed`, `${size(file.size)} / ${size(file.limit)}`);
  if (file.kind === "skill") parts.push(`Skill for ${file.backend === "codex" ? "Codex" : "Claude"}`, `${size(file.size)}`);
  if (file.updated) parts.push(`changed ${day(file.updated)} by ${who(file.written_by)}`);
  return parts.join(" · ");
}

/** A change's summary with channel IDs as #names. */
export function summaryText(summary: string, channels: { id: string; name: string | null }[]) {
  return summary.replace(/\bchannel ([A-Z0-9]{2,32})\b/g, (whole, id: string) => {
    const name = channels.find((c) => c.id === id)?.name;
    return name ? `#${name}` : whole;
  });
}

export const ACTION_SIGN: Record<string, "+" | "~" | "−"> = { saved: "+", added: "+", started: "+", forgot: "−", undid: "~" };

export function actionWords(change: Change) {
  const by = change.via === "tag-app" || change.via === "cli" ? "you" : who(change.written_by);
  return `${day(change.at, true)} · ${change.action === "outside" ? "changed outside Tag" : by}`;
}

// ---- A small markdown reader ----------------------------------------------------------
// Only what memory needs, rendered as React text (never HTML): paragraphs, headings,
// lists, **bold**, *italic*, `code`, [links](https://…) and [[key]] links.

export type Inline = { t: "text" | "b" | "i" | "code"; v: string } | { t: "link"; v: string; href: string } | { t: "key"; v: string };
export type Block = { t: "p"; v: Inline[] } | { t: "h"; level: number; v: Inline[] } | { t: "ul" | "ol"; items: Inline[][] };

export function inline(text: string): Inline[] {
  const out: Inline[] = [];
  const pattern = /\[\[([a-z0-9][a-z0-9-]{0,63})\]\]|\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)|`([^`]+)`|\*\*([^*]+)\*\*|\*([^*\s][^*]*)\*/g;
  let last = 0;
  for (const m of text.matchAll(pattern)) {
    if (m.index! > last) out.push({ t: "text", v: text.slice(last, m.index) });
    if (m[1]) out.push({ t: "key", v: m[1] });
    else if (m[2]) out.push({ t: "link", v: m[2], href: m[3] });
    else if (m[4]) out.push({ t: "code", v: m[4] });
    else if (m[5]) out.push({ t: "b", v: m[5] });
    else out.push({ t: "i", v: m[6] });
    last = m.index! + m[0].length;
  }
  if (last < text.length) out.push({ t: "text", v: text.slice(last) });
  return out;
}

export function blocks(text: string): Block[] {
  const out: Block[] = [];
  let para: string[] = [];
  const flush = () => { if (para.length) out.push({ t: "p", v: inline(para.join("\n")) }); para = []; };
  for (const raw of text.replace(/\r\n/g, "\n").split("\n")) {
    const line = raw.replace(/^\\(\\*## )/, "$1");
    const heading = /^(#{1,6})\s+(.*)$/.exec(raw);
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (!line.trim()) { flush(); continue; }
    if (heading) { flush(); out.push({ t: "h", level: heading[1].length, v: inline(heading[2]) }); continue; }
    if (bullet || numbered) {
      flush();
      const kind = bullet ? "ul" : "ol";
      const prev = out[out.length - 1];
      const item = inline((bullet ?? numbered)![1]);
      if (prev && prev.t === kind) prev.items.push(item); else out.push({ t: kind, items: [item] });
      continue;
    }
    para.push(line);
  }
  flush();
  return out;
}

/** Every memory path in the tree, for `[[key]]` links: a channel's own note first, then all channels'. */
export function findNote(list: KnowledgeList, key: string, channel: string | null | undefined) {
  const own = list.channels.find((c) => c.id === channel)?.notes.find((n) => n.key === key);
  return own ?? list.memory.notes.find((n) => n.key === key) ?? null;
}

// ---- Sample data -------------------------------------------------------------------

/** In-memory `tag NAME knowledge …` for the demo app, seeded from the protocol examples. */
export function demoKnowledge() {
  const list = structuredClone(listExample) as unknown as KnowledgeList & { schema_version: number };
  const files = new Map<string, KnowledgeFile>();
  const coreFile = structuredClone(readExample.file) as unknown as KnowledgeFile;
  files.set(coreFile.path, coreFile);
  files.set("memory/core.md", { path: "memory/core.md", kind: "core", version: "v-all", scope: "all", channel: null, channel_name: null,
    body: "## tone\nReplies use British English and stay short.\n", size: 43, limit: 6000,
    entries: [{ key: "tone", text: "Replies use British English and stay short.", version: "v-tone", links: [], written_by: "U0MAYA001",
      from_thread: "hover-tag://tag/t0klovr1-a0rese02/thread/C0LAUNCH1/1760800000.000400", updated: "2026-10-20T08:00:00Z" }],
    written_by: "U0MAYA001", from_thread: null, updated: "2026-10-20T08:00:00Z" });
  files.set("memory/channels/C0OPS0001/notes/digest-sources.md", { path: "memory/channels/C0OPS0001/notes/digest-sources.md", kind: "note",
    version: "v-src", scope: "channel", channel: "C0OPS0001", channel_name: "ops", key: "digest-sources", links: [],
    body: "Merged pull requests in hover-tag, closed issues, and the #launch decisions thread.", size: 83, limit: 4000,
    written_by: "U0JULES01", from_thread: "hover-tag://tag/t0klovr1-a0rese02/thread/C0OPS0001/1760700000.000300", updated: "2026-10-15T11:05:00Z" });
  files.set("skills/claude/weekly-digest/SKILL.md", { path: "skills/claude/weekly-digest/SKILL.md", kind: "skill", version: "v-skill",
    name: "weekly-digest", backend: "claude", summary: "Post the Monday ops digest in five linked bullets.", size: 195, limit: 200000,
    body: "# Weekly digest\n\n1. Collect last week's merged work.\n2. Write at most five bullets, each with a link.\n",
    written_by: null, from_thread: null, updated: null });
  const changes: Change[] = structuredClone(historyExample.changes) as Change[];
  const before = new Map<string, { path: string; body: string | null; after: string | null }>();
  let counter = 0;
  const fail = (error: string, code = "refused") => ({ ok: false, error, code });
  const sizeOf = (f: KnowledgeFile) => f.kind === "core" ? (f.entries ?? []).reduce((n, e) => n + e.text.length, 0) : f.body.length;
  const parse = (body: string) => body.split(/^## /m).filter((s) => s.trim()).map((s) => {
    const [key, ...rest] = s.split("\n");
    return { key: key.trim(), text: rest.join("\n").replace(/^\n+|\n+$/g, "") };
  });
  const record = (path: string, old: string | null, now: string | null, action: string, summary: string, undoOf: string | null = null) => {
    const id = `demo${String(++counter).padStart(36, "0")}`;
    before.set(id, { path, body: old, after: now });
    const change: Change = { id, at: new Date().toISOString(), action, summary, written_by: "local-operator", via: "tag-app",
      from_thread: null, entries: [path], undo_of: undoOf, undoable: true, undone_by: null };
    changes.unshift(change);
    if (undoOf) { const done = changes.find((c) => c.id === undoOf); if (done) done.undone_by = id; }
    return change;
  };
  const write = (path: string, body: string | null) => {
    const file = files.get(path);
    if (!file) return;
    if (body === null) { files.delete(path); return; }
    file.body = body;
    if (file.kind === "core") {
      const old = new Map((file.entries ?? []).map((e) => [e.key, e]));
      file.entries = parse(body).map(({ key, text }) => old.get(key)?.text === text ? old.get(key)! :
        { key, text, version: `v${++counter}`, links: [...text.matchAll(/\[\[([a-z0-9-]+)\]\]/g)].map((m) => m[1]),
          written_by: "local-operator", from_thread: null, updated: new Date().toISOString() });
    }
    file.version = `v${++counter}`;
    file.size = sizeOf(file);
    file.written_by = "local-operator";
    file.updated = new Date().toISOString();
  };
  return (args: string[]): Record<string, unknown> => {
    const [action, target] = args;
    const flag = (name: string) => args.includes(name) ? args[args.indexOf(name) + 1] : undefined;
    const value = (name: string) => args.find((a) => a.startsWith(`${name}=`))?.slice(name.length + 1) ?? flag(name);
    if (action === "list") {
      for (const [path, file] of files) {
        const node = path === "memory/core.md" ? list.memory.core : list.channels.find((c) => c.core?.path === path)?.core;
        if (node) Object.assign(node, { size: file.size, entries: file.entries?.length ?? 0, updated: file.updated, written_by: file.written_by });
      }
      return { ok: true, ...list };
    }
    if (action === "read") {
      const file = files.get(target);
      return file ? { ok: true, file: structuredClone(file) } : fail(`There is no ${target}.`, "not_found");
    }
    if (action === "edit") {
      const file = files.get(target);
      if (!file) return fail(`There is no ${target}.`, "not_found");
      if (flag("--base") !== file.version) return fail("This file changed since you opened it. Reload it, then edit again.", "conflict");
      const body = value("--body") ?? "";
      if (/ignore (all |any )?(the )?(previous|prior) instructions/i.test(body)) return fail("Not saved: the text looks like an instruction to override Tag's rules.", "unsafe");
      const old = file.body;
      write(target, body);
      if (file.kind === "core" && file.size > 6000) { write(target, old); return fail("Not saved: always-loaded memory would be over 6,000 characters.", "core_full"); }
      return { ok: true, change: record(target, old, body, "edited", `Edited ${file.kind === "skill" ? "skill " + file.name : file.key ?? whereLabel(file) + " memory"}`), file: structuredClone(file) };
    }
    if (action === "add") {
      const key = value("--key")!, text = value("--text")!, channel = flag("--channel");
      const kind = (flag("--kind") ?? "core") as "core" | "note";
      const prefix = channel ? `memory/channels/${channel}/` : "memory/";
      const name = list.channels.find((c) => c.id === channel)?.name ?? null;
      if (kind === "note") {
        const path = `${prefix}notes/${key}.md`;
        if (files.has(path)) return fail(`'${key}' already exists.`, "exists");
        files.set(path, { path, kind: "note", version: `v${++counter}`, scope: channel ? "channel" : "all", channel: channel ?? null, channel_name: name,
          key, links: [], body: text, size: text.length, limit: 4000, written_by: "local-operator", from_thread: null, updated: new Date().toISOString() });
        const node: Node = { path, kind: "note", key, summary: text.slice(0, 80), size: text.length, limit: 4000, written_by: "local-operator", from_thread: null, updated: new Date().toISOString() };
        (channel ? list.channels.find((c) => c.id === channel)!.notes : list.memory.notes).push(node);
        return { ok: true, change: record(path, null, text, "saved", `Saved ${key}`), file: structuredClone(files.get(path)) };
      }
      const path = `${prefix}core.md`;
      if (!files.has(path)) {
        files.set(path, { path, kind: "core", version: "v0", scope: channel ? "channel" : "all", channel: channel ?? null, channel_name: name,
          body: "", entries: [], size: 0, limit: 6000, written_by: null, from_thread: null, updated: null });
        const node: Node = { path, kind: "core", entries: 0, size: 0, limit: 6000, written_by: null, from_thread: null, updated: null };
        if (channel) list.channels.find((c) => c.id === channel)!.core = node; else list.memory.core = node;
      }
      const file = files.get(path)!;
      if (file.entries!.some((e) => e.key === key)) return fail(`'${key}' already exists.`, "exists");
      const old = file.body;
      write(path, coreBody([...file.entries!, { key, text }]));
      return { ok: true, change: record(path, old, file.body, "saved", `Saved ${key}`), file: structuredClone(file) };
    }
    if (action === "forget") {
      const file = files.get(target);
      if (!file) return fail(`There is no ${target}.`, "not_found");
      const old = file.body;
      if (file.kind === "note") {
        write(target, null);
        for (const notes of [list.memory.notes, ...list.channels.map((c) => c.notes)]) {
          const at = notes.findIndex((n) => n.path === target);
          if (at >= 0) notes.splice(at, 1);
        }
        return { ok: true, change: record(target, old, null, "forgot", `Forgot ${file.key}`) };
      }
      const key = flag("--key")!;
      write(target, withEntry(file.entries ?? [], key, null));
      return { ok: true, change: record(target, old, file.body, "forgot", `Forgot ${key}`), file: structuredClone(file) };
    }
    if (action === "history") {
      const path = target && !target.startsWith("--") ? target : null;
      return { ok: true, changes: changes.filter((c) => !path || c.entries.some((e) => e.split("#")[0] === path)), has_more: false };
    }
    if (action === "show" || action === "undo") {
      const change = changes.find((c) => c.id === target);
      if (!change) return fail(`No change ${target} in this Tag's history.`, "not_found");
      const saved = before.get(target);
      if (action === "show" && !saved && target === showExample.change.id) return { ok: true, change, files: showExample.files };
      if (action === "show") {
        const old = saved?.body ?? "", now = saved?.after ?? "";
        const diff: DiffLine[] = [...old.split("\n").filter((l) => !now.split("\n").includes(l)).map((text) => ({ op: "-" as const, text })),
          ...now.split("\n").filter((l) => !old.split("\n").includes(l)).map((text) => ({ op: "+" as const, text }))];
        return { ok: true, change, files: saved ? [{ path: saved.path, status: old ? now ? "changed" : "removed" : "added", before: old, after: now, diff }] : [] };
      }
      if (!saved) return fail("That change happened before this demo started.", "not_undoable");
      if (change.undone_by) return fail("That change is already undone.", "not_undoable");
      const file = files.get(saved.path);
      if ((file?.body ?? null) !== saved.after) return fail("This changed again after that change. Undo the later change first.", "conflict");
      if (file) write(saved.path, saved.body);
      return { ok: true, change: record(saved.path, saved.after, saved.body, "undid", `Undid: ${change.summary}`, change.id) };
    }
    return fail(`Unknown knowledge command ${action}.`);
  };
}
