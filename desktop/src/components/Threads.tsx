// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Threads: one list of every Tag's conversations, the open conversation in Markdown,
// a reply box, and a side panel for the files and memory a thread produced.
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import type { Bridge } from "../lib/bridge";
import type { ActivityItem } from "../lib/home";
import { Markdown } from "../lib/markdown";
import { useFresh, useListMotion } from "../lib/motion";
import { title, type TagRow } from "../lib/protocol";
import {
  ago, answerPrompt, arrange, placeOf, sendReply, STATUS_TEXT, threadKey, threadTitle, useThread,
  type ListedThread, type MemoryChange, type Prompt, type ThreadDetail, type ThreadFile, type ThreadItem, type Threads,
} from "../lib/threads";
import { ActivityDetails } from "./ActivityDetails";
import { Avatar, ErrorLine, fitText, Icon, Switch } from "./ui";

export type ThreadTarget = { tag: string; channel: string; thread_ts: string };

const clock = (iso: string | null | undefined) => {
  if (!iso) return "";
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};

const duration = (seconds: number | undefined) => seconds === undefined ? "" : seconds < 60 ? `${seconds}s`
  : `${Math.floor(seconds / 60)}m${seconds % 60 ? ` ${seconds % 60}s` : ""}`;

/** Every Tag's threads: "Needs you" on top, then the newest; quiet finished ones fold into Settled. */
export function ThreadList(props: {
  threads: Threads; tags: TagRow[]; selected: string | null; select: (thread: ListedThread) => void;
  /** Only this Tag's threads, or every Tag's. */
  filter: string | null; query: string; newThread: (() => void) | null;
}) {
  // Rows animate in and out only once the list has loaded, so opening the screen stays calm.
  if (!props.threads.loaded) return <div className="th-list" role="listbox" aria-label="Threads"><div className="sl-empty"><span className="spin" /></div></div>;
  return <LoadedThreadList {...props} />;
}

function LoadedThreadList({ threads, tags, selected, select, filter, query, newThread }: Parameters<typeof ThreadList>[0]) {
  const [settledOpen, setSettledOpen] = useState(false);
  const q = query.trim().toLocaleLowerCase().replace(/^#/, "");
  const shown = threads.rows.filter((row) => (!filter || row.tag === filter) && (!q || [threadTitle(row), placeOf(row), row.channel_name ?? "",
    ...row.people, row.last_answer_preview ?? ""].some((text) => text.toLocaleLowerCase().includes(q))));
  const { active, settled } = arrange(shown);
  const listed = useListMotion(active, threadKey);
  const tagOf = (row: ListedThread) => tags.find((tag) => tag.id === row.tag) ?? null;
  return (
    <div className="th-list" role="listbox" aria-label="Threads">
      {!shown.length && (
        <div className="sl-empty th-none">
          <Icon name="chat" size={22} />
          <div className="label">{q ? "No threads match" : "No threads yet"}</div>
          {!q && <p className="meta">Mention a Tag in Slack and the conversation shows here.</p>}
          {!q && newThread && <button className="link" onClick={newThread}>Start one here</button>}
        </div>
      )}
      {listed.map(({ item: row, state }) => (
        <ThreadRowButton key={threadKey(row)} row={row} tag={tagOf(row)} on={selected === threadKey(row)} select={() => select(row)}
          className={state === "enter" ? "entering" : state === "leave" ? "leaving" : ""} />
      ))}
      {settled.length > 0 && <>
        <button className="th-group" aria-expanded={settledOpen} onClick={() => setSettledOpen(!settledOpen)}>
          Settled <span className="tcount">{settled.length}</span><Icon name="chevdown" size={12} />
        </button>
        {settledOpen && <div className="th-settled">
          {settled.map((row) => {
            const tag = tagOf(row);
            return (
              <button key={threadKey(row)} role="option" aria-selected={selected === threadKey(row)}
                className={selected === threadKey(row) ? "th-set on" : "th-set"} onClick={() => select(row)}>
                <Avatar row={tag} size={18} badge={false} className="" still />
                <span className="x">{threadTitle(row)}</span><span className="when">{ago(row.updated_at)}</span>
              </button>
            );
          })}
        </div>}
      </>}
      {threads.failed.length > 0 && <p className="meta th-failed">Couldn't read the threads of {threads.failed.map((id) => {
        const tag = tags.find((t) => t.id === id); return tag ? title(tag) : id; }).join(", ")}. Trying again.</p>}
    </div>
  );
}

function ThreadRowButton({ row, tag, on, select, className }: { row: ListedThread; tag: TagRow | null; on: boolean; select: () => void; className: string }) {
  // The status changing (such as to "Needs you") flashes once, never on first show.
  const changed = useFresh(row.status);
  const working = row.status === "working";
  return (
    <button role="option" aria-selected={on} className={`th-row${on ? " on" : ""} ${className}`} onClick={select}>
      <span className="r1">
        <Avatar row={tag} size={18} badge={false} className="" mood={working ? "work" : "idle"} still={!working} />
        <span className="src">{row.source === "app" ? "App" : "Slack"}</span>
        <span className="ch">{placeOf(row)}</span>
        <span className={`st ${row.status}${changed ? " fresh" : ""}`}>
          {row.status === "needs_you" && <i className="dot" />}
          {working && <span className="spin" />}
          {row.status === "needs_you" || working || row.status === "failed" || row.status === "stopped" ? STATUS_TEXT[row.status] : ago(row.updated_at)}
        </span>
      </span>
      <span className="r2">{threadTitle(row)}</span>
      <span className="r3">
        <span className="x">{row.preview_only ? "Preview from Activity" : row.people.length ? `${row.people.join(", ")} · ${row.message_count} ${row.message_count === 1 ? "message" : "messages"}` : row.last_answer_preview ?? ""}</span>
        {row.file_count > 0 && <span className="k" title={`${row.file_count} ${row.file_count === 1 ? "file" : "files"}`}><Icon name="file" size={11} />{row.file_count}</span>}
        {row.memory_count > 0 && <span className="k" title={`${row.memory_count} memory ${row.memory_count === 1 ? "change" : "changes"}`}><Icon name="spark" size={11} />{row.memory_count}</span>}
      </span>
    </button>
  );
}

/** What the side panel can show, by tab. */
const panelTabs = (thread: ThreadDetail | null) => thread ? ([
  ["files", `Files · ${thread.files.length}`, thread.files.length],
  ["memory", `Memory · ${thread.memory.length}`, thread.memory.length],
] as const).filter(([, , count]) => count > 0) : [];

/** One conversation: messages and answers in order, with steps folded, prompts, and a reply box. */
export function Conversation({ api, target, tag, canReply, canAnswer, say, changed, openKnowledge, startTag }: {
  api: Bridge; target: ThreadTarget; tag: TagRow | null; canReply: boolean; canAnswer: boolean; say: (text: string) => void;
  /** Something was sent: read the list again. */
  changed: () => void;
  openKnowledge: (() => void) | null;
  startTag: () => void;
}) {
  const open = useThread(api, target);
  const thread = open.thread;
  const [panel, setPanel] = useState<"files" | "memory" | null>(null);
  const [hidden, setHidden] = useState(false);
  const tabs = panelTabs(thread);
  // The panel opens by itself only when there is something to show.
  const tab = tabs.find(([key]) => key === panel)?.[0] ?? tabs[0]?.[0] ?? null;
  const showPanel = !!tab && !hidden;
  const name = tag ? title(tag) : "Tag";
  const [focus, setFocus] = useState<{ kind: "file" | "memory"; key: string } | null>(null);
  const show = (kind: "files" | "memory", key: string) => { setPanel(kind); setHidden(false); setFocus({ kind: kind === "files" ? "file" : "memory", key }); };
  const body = useRef<HTMLDivElement>(null);
  const atEnd = useRef(true);
  const count = thread?.items.length ?? 0;
  useLayoutEffect(() => {
    const scroller = body.current;
    if (scroller && atEnd.current) scroller.scrollTop = scroller.scrollHeight;
  }, [count, thread?.status]);
  const refresh = () => { void open.refresh(); changed(); };
  // The list follows when this thread starts or stops working or waiting.
  const state = thread?.status;
  const changedRef = useRef(changed);
  changedRef.current = changed;
  const seenState = useRef(state);
  useEffect(() => {
    if (seenState.current !== undefined && state !== undefined && state !== seenState.current) changedRef.current();
    seenState.current = state;
  }, [state]);
  // Items that arrive while the conversation is open rise in; the ones it opened with stay put.
  const opened = useRef<number | null>(null);
  if (thread && opened.current === null) opened.current = thread.items.length;
  return (
    <div className={showPanel ? "th-open with-panel" : "th-open"}>
      <div className="th-main">
        <div className="th-head">
          <Avatar row={tag} size={24} badge={false} className="" still />
          <div className="txt">
            <b title={thread ? threadTitle(thread) : undefined}>{thread ? threadTitle(thread) : " "}</b>
            {thread && <span>{name} · {thread.source === "app" ? "Tag.app" : "Slack"} · {placeOf(thread)}</span>}
          </div>
          {thread && tabs.length > 0 && hidden && (
            <button className="p-btn soft sm" onClick={() => setHidden(false)}>{tabs.map(([, label]) => label).join(" · ")}</button>
          )}
          {thread?.team && (
            <button className="p-btn soft sm" onClick={() => void api.open(`slack://channel?team=${encodeURIComponent(thread.team!)}&id=${encodeURIComponent(thread.channel)}`)}>
              Open in Slack <Icon name="external" />
            </button>
          )}
        </div>
        <div ref={body} className="th-body" onScroll={(event) => {
          const el = event.currentTarget; atEnd.current = el.scrollHeight - el.clientHeight - el.scrollTop < 60;
        }}>
          {open.missing && (
            <div className="sl-empty th-missing">
              <Icon name="warn" size={22} />
              <div className="label">{open.missing}</div>
            </div>
          )}
          {open.error && <ErrorLine>{open.error}</ErrorLine>}
          {!thread && !open.missing && !open.error && <div className="sl-empty"><span className="spin" /></div>}
          {thread?.preview_only && <p className="meta th-preview">Saved before Tag kept whole conversations, so only a short preview of each reply is here.</p>}
          {thread?.items.map((item, index) => (
            <div key={itemKey(item, index)} className={index >= (opened.current ?? 0) ? "th-item fresh" : "th-item"}>
              <Item api={api} item={item} thread={thread} tag={tag} name={name} target={target}
                canAnswer={canAnswer} answered={refresh} say={say} show={show} />
            </div>
          ))}
        </div>
        {thread && canReply && !thread.preview_only && (
          <Composer api={api} target={target} tag={tag} name={name} sent={refresh} say={say} startTag={startTag} />
        )}
      </div>
      {showPanel && thread && (
        <SidePanel api={api} thread={thread} tab={tab!} tabs={tabs} pick={setPanel} close={() => setHidden(true)} focus={focus}
          openKnowledge={openKnowledge} />
      )}
    </div>
  );
}

const itemKey = (item: ThreadItem, index: number) =>
  item.type === "prompt" ? `p:${item.prompt_id}` : item.type === "work" ? `w:${item.run_id}` : `${item.type}:${index}`;

function Item({ api, item, thread, tag, name, target, canAnswer, answered, say, show }: {
  api: Bridge; item: ThreadItem; thread: ThreadDetail; tag: TagRow | null; name: string; target: ThreadTarget;
  canAnswer: boolean; answered: () => void; say: (text: string) => void; show: (kind: "files" | "memory", key: string) => void;
}) {
  const link = (url: string) => void api.open(url);
  if (item.type === "message") {
    const mine = item.via === "app";
    const who = mine ? "You" : item.author?.name ?? (item.author?.bot ? "A bot" : "Someone");
    return (
      <div className={`th-msg${mine ? " mine" : ""}${item.author?.bot ? " bot" : ""}`}>
        <span className="who">{who} · {mine ? (item.posted_to_slack ? "in Tag.app, also posted in Slack" : "in Tag.app") : "in Slack"} · {clock(item.at)}</span>
        <Markdown text={item.text} open={link} />
      </div>
    );
  }
  if (item.type === "work") return <Work api={api} item={item} thread={thread} tag={tag} name={name} />;
  if (item.type === "answer") {
    return (
      <div className={`th-ans${item.outcome !== "completed" ? " bad" : ""}`}>
        <span className="who"><Avatar row={tag} size={18} badge={false} className="" still />{name} · {clock(item.at)}
          {item.posted_to === "app" && <span className="only">Only in Tag.app</span>}
          {item.preview_only && <span className="only">Preview</span>}
        </span>
        <Markdown text={item.text} open={link} />
        {(item.files.length > 0 || item.memory.length > 0) && (
          <div className="th-chips">
            {item.files.map((file, index) => (
              <button key={`f${index}`} className="chip file" onClick={() => show("files", fileKey(file))}><Icon name="file" size={12} />{file.name}</button>
            ))}
            {item.memory.map((change, index) => (
              <button key={`m${index}`} className="chip mem" onClick={() => show("memory", memoryKey(change))}>
                <Icon name="spark" size={12} />{MEMORY_VERB[change.action] ?? "Changed"}: {change.summary ?? change.key}
              </button>
            ))}
          </div>
        )}
      </div>
    );
  }
  if (item.type === "prompt") return <PromptCard api={api} prompt={item} target={target} canAnswer={canAnswer} answered={answered} say={say} />;
  return <p className="th-notice">{item.text}</p>;
}

/** A run's steps, folded into one line that opens the step list. */
function Work({ api, item, thread, tag, name }: { api: Bridge; item: Extract<ThreadItem, { type: "work" }>; thread: ThreadDetail; tag: TagRow | null; name: string }) {
  const [open, setOpen] = useState(false);
  const running = item.status === "running";
  const current = running ? [...item.steps].reverse().find((step) => step.status === "running") : undefined;
  const failed = item.steps.filter((step) => step.status === "failed").length;
  const text = running ? `${name} is working${current ? ` · ${current.label.replace(/…$/, "")}…` : "…"}`
    : item.status === "failed" ? `${name} couldn't finish` : item.status === "interrupted" ? `${name} stopped before it finished`
    : `${name} worked${item.duration_seconds ? ` for ${duration(item.duration_seconds)}` : ""}`;
  const id = `steps-${item.run_id}`;
  // Runs from before transcripts only have their Activity record, which keeps the steps' details.
  const activity: ActivityItem = { run_id: item.run_id, at: item.finished_at ?? item.started_at ?? "", kind: item.status === "failed" ? "failed" : running ? "working" : "replied",
    channel: thread.channel, channel_name: thread.channel_name, dm: thread.dm, step_count: item.step_count };
  return (
    <div className={`th-work${running ? " live" : ""}${item.status === "failed" ? " bad" : ""}`}>
      <button className="th-steps" aria-expanded={open} aria-controls={id} disabled={!item.step_count} onClick={() => setOpen(!open)}>
        <Avatar row={tag} size={16} badge={false} className="" mood={running ? "work" : "idle"} still={!running} />
        <span className="t">{text}</span>
        {item.step_count > 0 && <span className="n">{item.step_count} {item.step_count === 1 ? "step" : "steps"}{failed > 0 && ` · ${failed} failed`}<Icon name="right" size={11} /></span>}
      </button>
      {open && (item.preview_only
        ? <ActivityDetails api={api} tag={tag?.id ?? ""} item={activity} open id={id} />
        : <ol id={id} className="th-step-list">
          {item.steps.map((step) => (
            <li key={step.id} className={step.status}><StepMark status={step.status} />{step.label}</li>
          ))}
        </ol>)}
    </div>
  );
}

function StepMark({ status }: { status: string }) {
  if (status === "running") return <span className="spin" aria-label="Running" />;
  if (status === "completed") return <span className="mk ok" aria-label="Done"><Icon name="check" size={11} /></span>;
  if (status === "failed" || status === "declined") return <span className="mk bad" aria-label={status === "failed" ? "Failed" : "Declined"}><Icon name="close" size={11} /></span>;
  return <span className="mk" aria-label="Stopped" />;
}

const KIND_TITLE: Record<string, string> = {
  approval: "Approve this action?", queue_steer: "Someone wrote while the Tag was working",
  run_request: "A teammate asked your Tag to run work", sign_in: "Sign in to continue",
};

/** A question that waits for the owner. Answering here closes Slack's copy too, and the other way round. */
function PromptCard({ api, prompt, target, canAnswer, answered, say }: {
  api: Bridge; prompt: Prompt; target: ThreadTarget; canAnswer: boolean; answered: () => void; say: (text: string) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  if (prompt.status !== "pending") {
    const answer = prompt.answer;
    return (
      <div className={`th-asked ${prompt.status}`}>
        <Icon name={prompt.status === "answered" ? "check" : "clock"} size={12} />
        <span className="q">{prompt.text}</span>
        {answer ? <b>{answer.by?.name ?? "Someone"}: {answer.label ?? answer.choice}</b> : <b className="gone">No longer waiting</b>}
        {answer && <em>{answer.via === "app" ? "in Tag.app" : "in Slack"} · {clock(answer.at)}</em>}
      </div>
    );
  }
  const choose = async (choice: string) => {
    setBusy(choice); setError("");
    const result = await answerPrompt(api, target, prompt.prompt_id, choice);
    setBusy(null);
    if (!result.ok) { setError(result.error); return; }
    say("Answer sent to the Tag");
    answered();
  };
  return (
    <div className="th-need" role="group" aria-label={KIND_TITLE[prompt.kind] ?? "Your Tag needs you"}>
      <span className="t"><Icon name="bang" size={13} />{KIND_TITLE[prompt.kind] ?? "Your Tag needs you"}</span>
      <p>{prompt.text}</p>
      <span className="acts">
        {prompt.url && <button className="p-btn ink sm" onClick={() => void api.open(prompt.url!)}>Sign in <Icon name="external" /></button>}
        {prompt.choices.map((choice) => (
          <button key={choice.id} className={`p-btn sm ${choice.style === "danger" ? "danger" : choice.primary || choice.style === "primary" ? "ink" : "soft"}`}
            disabled={!canAnswer || busy !== null} onClick={() => void choose(choice.id)}>
            {busy === choice.id ? "Sending…" : choice.label}
          </button>
        ))}
      </span>
      {error && <ErrorLine>{error}</ErrorLine>}
      <span className="meta">{canAnswer ? "Also sent to you privately in Slack. Answering in either place closes both." : "Answer it in Slack. Update Tag to answer here."}</span>
    </div>
  );
}

/** The reply box: replies stay in Tag.app unless this message is also posted in Slack. */
function Composer({ api, target, tag, name, sent, say, startTag }: {
  api: Bridge; target: ThreadTarget; tag: TagRow | null; name: string; sent: () => void; say: (text: string) => void; startTag: () => void;
}) {
  const [text, setText] = useState("");
  const [post, setPost] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);
  const running = tag?.state === "running";
  useEffect(() => { setText(""); setPost(false); setError(""); }, [target.tag, target.channel, target.thread_ts]);
  const send = async () => {
    const reply = text.trim();
    if (!reply || busy) return;
    setBusy(true); setError("");
    const result = await sendReply(api, target, reply, post);
    setBusy(false);
    if (!result.ok) { setError(result.error); return; }
    setText(""); setPost(false);
    if (box.current) { box.current.value = ""; fitText(box.current); }
    say(post ? `Sent to ${name} and posted in Slack` : `Sent to ${name}`);
    sent();
  };
  if (!running) {
    return (
      <div className="th-compose off">
        <span className="meta">{name} replies only while it runs.</span>
        <button className="p-btn soft sm" onClick={startTag}><Icon name="play" size={10} />Start {name}</button>
      </div>
    );
  }
  return (
    <div className="th-compose">
      {error && <ErrorLine onDismiss={() => setError("")}>{error}</ErrorLine>}
      <div className="th-box">
        <textarea ref={box} rows={1} value={text} placeholder="Reply in this thread…" aria-label="Reply"
          onChange={(event) => { setText(event.target.value); fitText(event.currentTarget); }}
          onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }} />
        <button className="send" aria-label="Send" disabled={!text.trim() || busy} onClick={() => void send()}>
          {busy ? <span className="spin" /> : <Icon name="up" size={14} />}
        </button>
      </div>
      <div className="opts">
        <span>To <b>{name}</b></span>
        <label className="post">
          <Switch on={post} busy={false} label="Also post in Slack" onClick={() => setPost(!post)} />
          {post ? "Also posted in the Slack thread" : "Stays in Tag.app"}
        </label>
      </div>
    </div>
  );
}

const MEMORY_VERB: Record<string, string> = { saved: "Saved", changed: "Changed", forgot: "Forgot" };
const MEMORY_SIGN: Record<string, string> = { saved: "+", changed: "~", forgot: "−" };
const fileKey = (file: ThreadFile) => `${file.run_id}:${file.name}`;
const memoryKey = (change: MemoryChange) => `${change.run_id}:${change.key}:${change.version}`;

/** Files the thread made and what it remembered. Opens only when there is something to show. */
function SidePanel({ api, thread, tab, tabs, pick, close, focus, openKnowledge }: {
  api: Bridge; thread: ThreadDetail; tab: "files" | "memory"; tabs: ReturnType<typeof panelTabs>;
  pick: (tab: "files" | "memory") => void; close: () => void; focus: { kind: "file" | "memory"; key: string } | null;
  openKnowledge: (() => void) | null;
}) {
  return (
    <aside className="th-panel" aria-label="Files and memory">
      <div className="th-ptabs sl-tabs" role="tablist">
        {tabs.map(([key, label]) => <button key={key} role="tab" aria-selected={tab === key} onClick={() => pick(key)}>{label}</button>)}
        <button className="x" aria-label="Close panel" title="Close" onClick={close}><Icon name="close" size={12} /></button>
      </div>
      <div className="th-pbody" role="tabpanel">
        {tab === "files" && thread.files.map((file) => (
          <Focusable key={fileKey(file)} on={focus?.kind === "file" && focus.key === fileKey(file)} className="th-file">
            <Icon name={file.kind === "image" ? "image" : "file"} size={16} />
            <div className="txt"><b>{file.name}</b>
              <span className="meta">{file.delivery === "uploaded" ? "Shared in Slack" : file.delivery === "local" ? "On this computer" : "Couldn't upload"} · {ago(file.at)}</span></div>
            <span className="acts">
              {file.local_path && <button className="link" onClick={() => void api.open(file.local_path!)}>Open</button>}
              {file.url && <button className="link" onClick={() => void api.open(file.url!)}>Slack <Icon name="external" size={11} /></button>}
            </span>
          </Focusable>
        ))}
        {tab === "memory" && <>
          {thread.memory.map((change) => (
            <Focusable key={memoryKey(change)} on={focus?.kind === "memory" && focus.key === memoryKey(change)} className="th-mem">
              <span className={`sg ${change.action}`}>{MEMORY_SIGN[change.action] ?? "~"}</span>
              <div className="txt">
                <span>{MEMORY_VERB[change.action] ?? "Changed"} <b>{change.key}</b></span>
                {change.summary && <span className="sum">{change.summary}</span>}
                <span className="meta">{change.scope === "global" ? "All channels" : change.scope ? "This channel" : "Memory"}{change.version && <> · version <span className="mono">{change.version.slice(0, 7)}</span></>} · {ago(change.at)}</span>
              </div>
            </Focusable>
          ))}
          {openKnowledge && <button className="p-btn soft sm th-review" onClick={openKnowledge}>Review in Knowledge</button>}
        </>}
      </div>
    </aside>
  );
}

function Focusable({ on, className, children }: { on: boolean; className: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { if (on) ref.current?.scrollIntoView?.({ block: "nearest" }); }, [on]);
  return <div ref={ref} className={`${className}${on ? " on" : ""}`}>{children}</div>;
}
