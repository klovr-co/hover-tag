// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Tag detail, thread first: every Tag's conversations in one list beside the open one,
// the picked Tag's Knowledge, and its Channels, Routines, Logs and Details under ⚙.
// A Tag picker in the top bar chooses which Tag Knowledge and ⚙ show.
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ComponentType, type ReactNode } from "react";
import type { Bridge } from "../lib/bridge";
import { effortLabel, effortShort, generationTime, liveRows, modelText, placeText, type ActivityItem } from "../lib/home";
import { ActivityDetails, StepsToggle } from "./ActivityDetails";
import { ActivityArtifacts } from "./ActivityArtifacts";
import { ActivityTokens } from "./ActivityTokens";
import { useModelChoice } from "../lib/model";
import { usePresence } from "../lib/motion";
import { usePermissions } from "../lib/permissions";
import { collapseRepeats, groups, parseJSON, problemHelp, problemText, status, title, type TagRow } from "../lib/protocol";
import type { Tags } from "../lib/tags";
import { threadKey, type ListedThread, type Threads } from "../lib/threads";
import teamArt from "../assets/art/tag-team.png";
import keyArt from "../assets/art/tag-key.png";
import { workingFolder } from "./Home";
import { modelSaveText, ModelMenu, ModelWarning, PermissionsRow, SaveBar, ThinkingRow } from "./AI";
import { Routines } from "./Routines";
import { Conversation, ThreadList, type ThreadTarget } from "./Threads";
import { Avatar, dragWindow, ErrorLine, fitText, Icon, Primary, Switch, useCopied, WorkspaceMark, tagIcon } from "./ui";

/** The Tag's own settings, under ⚙. */
export type Tab = "channels" | "routines" | "logs" | "details";
export type View = "threads" | "knowledge" | "settings";

/**
 * Knowledge, and moving a Tag between computers, come from their own components when this
 * build has them; until then the Knowledge tab says it's on its way and the buttons stay away.
 */
type KnowledgeParts = {
  Knowledge?: ComponentType<{ api: Bridge; row: TagRow; say: (text: string) => void; openThread?: (tag: string, channel: string, ts: string) => void }>;
  ExportTag?: ComponentType<{ api: Bridge; row: TagRow; say: (text: string) => void }>;
  ImportTag?: ComponentType<{ api: Bridge; say: (text: string) => void; onImported: () => void }>;
};
const parts: KnowledgeParts = Object.assign({}, ...Object.values(
  import.meta.glob<KnowledgeParts>(["./Knowledge*.tsx", "./*Tag.tsx", "!./*.test.tsx"], { eager: true })));

interface Props {
  api: Bridge;
  tags: Tags;
  /** The Tag that was opened from Home. */
  initial: string;
  /** Open this Tag's settings at this tab; a Slack link to a Tag opens Details. */
  initialTab?: Tab;
  /** Open this thread first; a Slack link to a thread opens it. */
  initialThread?: { channel: string; thread: string };
  problems: Record<string, string | null>;
  back: () => void;
  add: () => void;
  showSettings: () => void;
  finishSetup: (row: TagRow) => void;
  openAI: (tag: string) => void;
  say: (text: string) => void;
  /** Every Tag's threads (`threads-json`); without it, the Threads tab shows the Tag's Activity. */
  threads?: Threads;
  /** The installed Tag can take replies (`thread-reply`) and answers (`thread-prompts`) from the app. */
  canReply?: boolean;
  canAnswer?: boolean;
  /** The installed Tag can change descriptions (`describe` capability). */
  canDescribe?: boolean;
  /** The installed Tag has routines (`routines` capability). */
  canRoutines?: boolean;
  /** The installed Tag can read model lists again (`saved-models` capability). */
  canRefreshModels?: boolean;
}

const PRESENCE = { online: "var(--green)", offline: "transparent", setup: "var(--amber)", attention: "var(--red)" };
const WORD = { online: "Online", offline: "Offline", setup: "Not in Slack yet", attention: "Stopped" };

/** "~/Tag/maya" rather than the whole path. */
export const shortPath = (path: string) => path.replace(/^(\/Users\/[^/]+|\/home\/[^/]+|[A-Z]:\\Users\\[^\\]+)/, "~");

/** A channel's name for people, or its ID while Tag hasn't recorded one. */
const channelName = (channel: { id: string; name: string | null }) => channel.name ?? channel.id;

export function TagDetail({ api, tags, initial, initialTab, initialThread, problems, back, add, showSettings, finishSetup, openAI, say, threads,
  canReply = false, canAnswer = false, canDescribe = false, canRefreshModels = false, canRoutines = false }: Props) {
  const rows = tags.rows;
  const [picked, setPicked] = useState(initial);
  const [view, setView] = useState<View>(initialTab ? "settings" : "threads");
  const [tab, setTab] = useState<Tab>(initialTab ?? "channels");
  // A Tag too old to report threads shows its Activity here, beside its other tabs.
  const [olderTab, setOlderTab] = useState<PaneTab>("activity");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<string | null>(null);
  const [hideErrors, setHideErrors] = useState(true);
  const [open, setOpen] = useState<ThreadTarget | null>(initialThread ? { tag: initial, channel: initialThread.channel, thread_ts: initialThread.thread } : null);
  const [starting, setStarting] = useState(false);
  const row = rows.find((r) => r.id === picked) ?? liveRows(rows)[0] ?? rows[0];
  // With nothing open, the thread at the top of the list opens.
  const first = threads?.rows.find((thread) => thread.status === "needs_you") ?? threads?.rows[0];
  useEffect(() => {
    if (!open && view === "threads" && first) setOpen({ tag: first.tag, channel: first.channel, thread_ts: first.thread_ts });
  }, [open, view, first]);
  const pick = (id: string) => { setPicked(id); if (view === "threads" && filter) setFilter(id); };
  const select = (thread: ListedThread) => { setOpen({ tag: thread.tag, channel: thread.channel, thread_ts: thread.thread_ts }); setPicked(thread.tag); };
  const top = (
    <div className="sl-top" onMouseDown={dragWindow}>
      <button className="sky-btn" onClick={back} aria-label="Back to Your Tags"><Icon name="left" />Your Tags</button>
      {row && <TagPicker api={api} rows={rows} row={row} pick={pick} add={add} showSettings={showSettings} say={say} refresh={() => void tags.refresh()} />}
      <div className="sl-views" role="tablist" aria-label="View">
        <button role="tab" aria-selected={view === "threads"} onClick={() => setView("threads")}>Threads</button>
        <button role="tab" aria-selected={view === "knowledge"} onClick={() => setView("knowledge")}>Knowledge</button>
      </div>
      {view === "threads" ? (
        <label className="sl-search"><Icon name="search" size={16} />
          <input placeholder="Search threads" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search threads" />
        </label>
      ) : <span className="sl-gap" />}
      <button className={view === "settings" ? "sky-btn on" : "sky-btn"} aria-label={row ? `${title(row)} settings` : "Tag settings"} aria-pressed={view === "settings"}
        title="Channels, routines, logs and details" onClick={() => setView(view === "settings" ? "threads" : "settings")}><Icon name="gear" size={16} /></button>
      <button className="add" onClick={add}><Icon name="plus" />Add Tag</button>
    </div>
  );
  if (!row) {
    return (
      <>
        {top}
        <div className="sl-grid" style={{ gridTemplateColumns: "1fr" }}>
          <div className="sl-main"><div className="sl-empty">
            <img src={teamArt} alt="" style={{ width: 300, imageRendering: "pixelated" }} />
            <div className="h2">Bring your first Tag to Slack</div>
            <p className="lead">Connect a workspace and Tag sets up its own Slack app.</p>
            <Primary title="Add your first Tag" icon="plus" onClick={add} />
          </div></div>
        </div>
      </>
    );
  }
  const live = liveRows(rows);
  const openTag = open ? rows.find((r) => r.id === open.tag) ?? null : null;
  const startTag = (tag: TagRow | null) => {
    if (!tag || starting) return;
    setStarting(true);
    void Promise.resolve(tags.toggle(tag)).finally(() => setStarting(false));
  };
  const Knowledge = parts.Knowledge;
  return (
    <>
      {top}
      {view === "threads" && (threads ? (
        <div className="sl-grid th-grid">
          <div className="sl-side">
            <div className="th-side-head">
              <FilterMenu rows={live} filter={filter} setFilter={setFilter} />
              <NewThread row={row} api={api} />
            </div>
            <ThreadList threads={threads} tags={rows} selected={open ? threadKey(open) : null} select={select} filter={filter} query={query} newThread={null} />
          </div>
          <div className="sl-main">
            {open
              ? <Conversation key={threadKey(open)} api={api} target={open} tag={openTag} canReply={canReply} canAnswer={canAnswer} say={say}
                changed={threads.refresh} openKnowledge={Knowledge ? () => { if (openTag) setPicked(openTag.id); setView("knowledge"); } : null}
                startTag={() => startTag(openTag)} />
              : <div className="sl-empty"><img src={tagIcon} alt="" style={{ width: 48, borderRadius: 12 }} /><div className="label">Pick a thread</div></div>}
          </div>
        </div>
      ) : (
        <div className="sl-grid" style={{ gridTemplateColumns: "1fr" }}>
          <div className="sl-main">
            <TagPane key={`${row.id}:${hideErrors}`} hideErrors={hideErrors} setHideErrors={setHideErrors} api={api} tags={tags} row={row} tab={olderTab} setTab={setOlderTab}
              problem={problems[row.id] ?? null} finishSetup={finishSetup} openAI={openAI} say={say} canDescribe={canDescribe} canRefreshModels={canRefreshModels} canRoutines={canRoutines}
              withActivity />
          </div>
        </div>
      ))}
      {view === "knowledge" && (
        <div className="sl-grid" style={{ gridTemplateColumns: "1fr" }}>
          <div className="sl-main">
            {Knowledge && status(row) !== "setup"
              ? <Knowledge key={row.id} api={api} row={row} say={say}
                openThread={(tag, channel, ts) => { setOpen({ tag, channel, thread_ts: ts }); setView("threads"); }} />
              : <div className="sl-empty">
                <img src={keyArt} alt="" style={{ width: 88, imageRendering: "pixelated" }} />
                <div className="h2">{status(row) === "setup" ? `Finish setting up ${title(row)}` : "Knowledge is on its way"}</div>
                <p className="lead">{status(row) === "setup" ? "Its memory starts once it's in Slack." : `What ${title(row)} remembers, and the threads it learned it in, will show here.`}</p>
              </div>}
          </div>
        </div>
      )}
      {view === "settings" && (
        <div className="sl-grid" style={{ gridTemplateColumns: "1fr" }}>
          <div className="sl-main">
            <TagPane key={`${row.id}:${hideErrors}`} hideErrors={hideErrors} setHideErrors={setHideErrors} api={api} tags={tags} row={row} tab={tab} setTab={(next) => setTab(next as Tab)}
              problem={problems[row.id] ?? null} finishSetup={finishSetup} openAI={openAI} say={say} canDescribe={canDescribe} canRefreshModels={canRefreshModels} canRoutines={canRoutines}
              exportTag={parts.ExportTag ? <parts.ExportTag api={api} row={row} say={say} /> : null} />
          </div>
        </div>
      )}
    </>
  );
}

/** A small menu that opens under its button, fades out when it closes, and closes on Escape or a click outside. */
function useMenu() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const shown = usePresence(open ? true : null, 120);
  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", close);
    return () => { window.removeEventListener("mousedown", close); window.removeEventListener("keydown", close); };
  }, [open]);
  return { open, setOpen, ref, shown: !!shown.shown, leaving: shown.leaving };
}

/** Chooses the Tag that Knowledge and ⚙ show; replaces the old icon rail and Tag header. */
function TagPicker({ api, rows, row, pick, add, showSettings, say, refresh }: {
  api: Bridge; rows: TagRow[]; row: TagRow; pick: (id: string) => void; add: () => void; showSettings: () => void; say: (text: string) => void; refresh: () => void;
}) {
  const menu = useMenu();
  const state = status(row);
  const Import = parts.ImportTag;
  return (
    <div className="tag-pick" ref={menu.ref} onMouseDown={(e) => e.stopPropagation()}>
      <button className="sky-btn pick" aria-haspopup="menu" aria-expanded={menu.open} aria-label={`Tag: ${title(row)}`} onClick={() => menu.setOpen(!menu.open)}>
        <Avatar row={row} size={22} badge={false} className="" />
        <span className="nm">{title(row)}</span>
        <span className="pd" style={{ background: PRESENCE[state], boxShadow: state === "offline" ? "inset 0 0 0 1.5px rgba(255,255,255,.7)" : undefined }} />
        <Icon name="chevdown" size={12} />
      </button>
      {menu.shown && (
        <div className={menu.leaving ? "menu tag-menu leaving" : "menu tag-menu"} role="menu" aria-label="Tags">
          {groups(rows).map((group) => (
            <div key={group.key} className="grp">
              {groups(rows).length > 1 && <div className="gl"><WorkspaceMark label={group.label} icon={group.icon} />{group.label}</div>}
              {group.rows.map((tag) => {
                const s = status(tag);
                return (
                  <button key={tag.id} role="menuitemradio" aria-checked={tag.id === row.id} onClick={() => { menu.setOpen(false); pick(tag.id); }}>
                    <Avatar row={tag} size={20} badge={false} className="" still />
                    <span className="nm">{title(tag)}</span>
                    <span className="st">{WORD[s]}</span>
                  </button>
                );
              })}
            </div>
          ))}
          <hr />
          <button role="menuitem" onClick={() => { menu.setOpen(false); add(); }}><Icon name="plus" />Add Tag</button>
          {Import && <Import api={api} say={say} onImported={() => { menu.setOpen(false); refresh(); }} />}
          <button role="menuitem" onClick={() => { menu.setOpen(false); showSettings(); }}><Icon name="gear" />Tag.app settings</button>
        </div>
      )}
    </div>
  );
}

/** Show every Tag's threads, or one Tag's. */
function FilterMenu({ rows, filter, setFilter }: { rows: TagRow[]; filter: string | null; setFilter: (id: string | null) => void }) {
  const menu = useMenu();
  const current = rows.find((row) => row.id === filter) ?? null;
  return (
    <div className="th-filter" ref={menu.ref}>
      <button className="link" aria-haspopup="menu" aria-expanded={menu.open} onClick={() => menu.setOpen(!menu.open)}>
        {current ? <><Avatar row={current} size={16} badge={false} className="" still />{title(current)}</> : "All Tags"}<Icon name="chevdown" size={11} />
      </button>
      {menu.shown && (
        <div className={menu.leaving ? "menu leaving" : "menu"} role="menu" aria-label="Show threads of">
          <button role="menuitemradio" aria-checked={!filter} onClick={() => { menu.setOpen(false); setFilter(null); }}>All Tags</button>
          {rows.map((row) => (
            <button key={row.id} role="menuitemradio" aria-checked={filter === row.id} onClick={() => { menu.setOpen(false); setFilter(row.id); }}>
              <Avatar row={row} size={18} badge={false} className="" still />{title(row)}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Starting a thread without Slack needs the Tag to hold a conversation outside Slack, which it can't yet.
 * Until it can, this says how to start one.
 */
function NewThread({ row, api }: { row: TagRow; api: Bridge }) {
  const menu = useMenu();
  return (
    <div className="th-new" ref={menu.ref}>
      <button className="sky-ico" aria-label="New thread" title="New thread" aria-haspopup="dialog" aria-expanded={menu.open} onClick={() => menu.setOpen(!menu.open)}>
        <Icon name="chat" size={15} />
      </button>
      {menu.shown && (
        <div className={menu.leaving ? "menu th-new-pop leaving" : "menu th-new-pop"} role="dialog" aria-label="New thread">
          <b>New threads start in Slack for now</b>
          <p>Mention <span className="mention">@{title(row)}</span> in a channel, or send it a direct message. The thread shows here as soon as it starts, and you can reply to it from here.</p>
          <button className="p-btn soft sm" onClick={() => { menu.setOpen(false); void api.open(`slack://open?team=${encodeURIComponent(row.slack_workspace ?? "")}`); }}>Open Slack <Icon name="external" /></button>
        </div>
      )}
    </div>
  );
}

const slackChannel = (team: string, id: string) => `slack://channel?team=${encodeURIComponent(team)}&id=${encodeURIComponent(id)}`;

const ACTIVITY_TEXT: Record<string, string> = { replied: "Replied in", failed: "Couldn't finish a request in", stopped: "Stopped a request in", working: "Working in" };

/** The Tag's own records of what it did, as a Slack-style feed; nothing else. */
export function activityText(item: ActivityItem) {
  return `${ACTIVITY_TEXT[item.kind] ?? "Worked in"} ${placeText(item)}${item.kind === "working" ? "…" : ""}`;
}

const clock = (d: Date) => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;

function dayLabel(at: Date, now: Date) {
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diff = Math.round((day(now) - day(at)) / 86400_000);
  return diff === 0 ? "Today" : diff === 1 ? "Yesterday" : at.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
}

/** "activity" is the Threads tab for a Tag too old to report threads. */
type PaneTab = Tab | "activity";

function TagPane({ api, tags, row, tab, setTab, problem, finishSetup, openAI, say, canDescribe, canRefreshModels, canRoutines, hideErrors, setHideErrors, exportTag = null, withActivity = false }: FilterProps & {
  api: Bridge; tags: Tags; row: TagRow; tab: PaneTab; setTab: (tab: PaneTab) => void; problem: string | null;
  finishSetup: (row: TagRow) => void; openAI: (tag: string) => void; say: (text: string) => void; canDescribe: boolean; canRefreshModels: boolean; canRoutines: boolean;
  exportTag?: ReactNode;
  /** Show the Tag's Activity as a tab, for a Tag too old to report threads. */
  withActivity?: boolean;
}) {
  const state = status(row);
  const [activity, setActivity] = useState<ActivityItem[] | null>(null);
  const [limit, setLimit] = useState(50);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [logText, setLogText] = useState("");
  const failure = usePresence(tags.error || null);
  const [logView, setLogView] = useState("");
  // A routine opened from its label in Activity, and text to find in the Logs tab.
  const [routineFocus, setRoutineFocus] = useState<string | null>(null);
  const [logFind, setLogFind] = useState("");
  const request = useRef(0);
  const load = useCallback(async () => {
    const current = ++request.current;
    setLoading(true);
    try {
      const result = await api.tag([row.id, "logs", "--json", "--limit", "200", "--activity-limit", String(limit), ...(hideErrors ? ["--hide-errors"] : [])]);
      if (current !== request.current) return;
      if (result.code !== 0) { setActivity((previous) => previous ?? []); return; }
      const logs = parseJSON<{ services: Record<string, string[]>; activity?: ActivityItem[]; activity_has_more?: boolean }>(result.stdout);
      setActivity((previous) => {
        const merged = new Map((previous ?? []).map((item) => [item.run_id ?? item.at, item]));
        for (const item of logs.activity ?? []) merged.set(item.run_id ?? item.at, item);
        return [...merged.values()];
      });
      setHasMore(logs.activity_has_more === true);
      const section = (fold: (lines: string[]) => string[]) => Object.entries(logs.services)
        .map(([name, lines]) => `── ${name} ──\n${fold(lines).join("\n") || "No recent entries"}`).join("\n\n");
      // Copy keeps every line; the Logs tab folds repeats so the cause stays visible.
      setLogText(section((lines) => lines));
      setLogView(section(collapseRepeats));
    } catch {
      if (current === request.current) setActivity((previous) => previous ?? []);
    } finally {
      if (current === request.current) setLoading(false);
    }
  }, [api, row.id, hideErrors, limit]);
  useEffect(() => {
    if (state === "setup") return;
    void load();
    const timer = setInterval(() => void load(), 30_000);
    return () => { request.current++; clearInterval(timer); };
  }, [load, state, tab]);

  if (state === "setup") {
    return (
      <div className="sl-empty">
        <img src={keyArt} alt="" style={{ width: 88, imageRendering: "pixelated" }} />
        <div className="h2">Finish setting up {title(row)}</div>
        <p className="lead">Setup stopped before it finished. Pick up where you left off, or remove this Tag.</p>
        <Primary title="Continue" after="arrow" onClick={() => finishSetup(row)} />
        <RemoveTag tags={tags} row={row} say={say} />
      </div>
    );
  }
  const on = row.state === "running";
  const channels = row.channels ?? [];
  const model = modelText(row)?.model ?? "";
  const effort = row.default_effort ? ` · ${effortShort(row.default_effort)}` : "";
  const copyLog = async () => {
    try { await api.copy(logText); say("Copied the full log"); return true; }
    catch { say("Could not copy the log"); return false; }
  };
  return (
    <>
      <div className="sl-mhead">
        <Avatar row={row} />
        <div className="txt">
          <span className="name"><span className="nm">{title(row)}</span>{row.main && <span className="main-badge" title="tag start without a name uses this Tag">Main</span>}</span>
          <span className={state === "attention" ? "sub bad" : "sub"}>
            <span>{state === "attention" ? problemText(row) : WORD[state]}{model && " · "}
              {model && <span style={problem ? { color: "var(--amber)", fontWeight: 600 } : undefined} title={problem ?? undefined}>{model}{effort}</span>}
              {channels.length > 0 && ` · in ${channels.length === 1 ? `#${channelName(channels[0])}` : `${channels.length} channels`}`}</span>
          </span>
        </div>
        {exportTag}
        <Switch on={on} busy={tags.busy.has(row.id)} label={`${on ? "Stop" : "Start"} ${title(row)}`} onClick={() => void tags.toggle(row)} />
      </div>
      <div className="sl-tabbar">
      <div className="sl-tabs" role="tablist">
        {([...(withActivity ? [["activity", "Activity", null] as const] : []), ["channels", "Channels", channels.length] as const,
          ...(canRoutines ? [["routines", "Routines", null] as const] : []), ["logs", "Logs", null] as const, ["details", "Details", null] as const])
          .map(([key, label, count]) => (
          <button key={key} role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>
            {label}{count !== null && <> <span className="tcount">{count}</span></>}
          </button>
        ))}
      </div>
      {tab === "activity" && <ActivityFilter hideErrors={hideErrors} setHideErrors={setHideErrors} />}
      </div>
      <div key={tab} className="sl-body" role="tabpanel">
        {/* Rename, description, remove and start/stop report failures here, not only on Home. */}
        {failure.shown && <ErrorLine leaving={failure.leaving} onDismiss={tags.dismissible ? () => tags.setError("") : undefined}>{failure.shown}</ErrorLine>}
        {tab === "activity" && <Activity hasMore={hasMore} loading={loading} loadOlder={() => setLimit((value) => value + 50)} hideErrors={hideErrors} api={api} row={row} items={activity} copyLog={copyLog} showLogs={() => { setLogFind(""); setTab("logs"); }} problem={problem} openAI={() => openAI(row.id)}
          openRoutine={canRoutines ? (id) => { setRoutineFocus(id); setTab("routines"); } : undefined} />}
        {tab === "channels" && (
          <>
            <p className="lead" style={{ margin: "0 0 8px" }}>{title(row)} answers and remembers conversations in these channels.</p>
            {channels.length ? (
              <div className="card">
                {channels.map((c) => (
                  <div key={c.id} className="opt">
                    <span className="hash">#</span><span className="label" style={{ flex: 1 }}>{channelName(c)}</span>
                    <button className="link" onClick={() => void api.open(slackChannel(row.slack_workspace ?? "", c.id))}>Open in Slack <Icon name="external" /></button>
                  </div>
                ))}
              </div>
            ) : (
              <p className="meta" style={{ margin: 0 }}>No channels yet. Type <span className="mono">/invite @{title(row)}</span> in a Slack channel to add it.</p>
            )}
          </>
        )}
        {tab === "routines" && canRoutines && <Routines api={api} row={row} say={say} focus={routineFocus}
          showLogs={(find) => { setLogFind(find); setTab("logs"); }} />}
        {tab === "logs" && <Logs text={logView} copyLog={copyLog} find={logFind} clearFind={() => setLogFind("")} />}
        {tab === "details" && <Details api={api} tags={tags} row={row} copyLog={copyLog} openAI={() => openAI(row.id)} say={say} canDescribe={canDescribe} canRefreshModels={canRefreshModels} />}
      </div>
      {on && tab === "activity" && (
        <div className="sl-compose">
          <span className="ph">Try it in Slack: <span className="mention">@{title(row)}</span> pull this thread into a launch checklist</span>
          <button className="p-btn ink sm" onClick={() => void api.open(`slack://open?team=${encodeURIComponent(row.slack_workspace ?? "")}`)}>Open Slack</button>
        </div>
      )}
    </>
  );
}

/** Remove a Tag: files are set aside; deleting its Slack app is a separate, typed confirmation. */
function RemoveTag({ tags, row, say }: { tags: Tags; row: TagRow; say: (text: string) => void }) {
  const name = title(row);
  const [step, setStep] = useState<"idle" | "choose" | "delete">("idle");
  const [typed, setTyped] = useState("");
  const appId = row.slack_app_id ?? "";
  const busy = tags.busy.has(row.id);
  const done = (ok: boolean, text: string) => { if (ok) say(text); else setStep("idle"); };
  if (step === "idle") return <button className="link bad" onClick={() => setStep("choose")}>Remove this Tag…</button>;
  if (step === "choose") {
    return (
      <div key="choose" className="notice" style={{ marginTop: 8 }}>
        <div style={{ flex: 1 }}>
          <div className="t">Remove {name}?</div>
          <div className="d">It stops, and its files are set aside in a backup. Its Slack app stays in Slack.</div>
        </div>
        <div className="act">
          <button className="p-btn quiet sm" onClick={() => setStep("idle")}>Cancel</button>
          {appId && <button className="link" onClick={() => setStep("delete")}>Also delete the Slack app…</button>}
          <button className="p-btn soft sm" disabled={busy}
            onClick={() => void tags.remove(row).then((ok) => done(ok, `Removed ${name}. Its Slack app is still in Slack.`))}>Remove</button>
        </div>
      </div>
    );
  }
  return (
    <div key="delete" className="notice bad" style={{ marginTop: 8 }}>
      <div style={{ flex: 1 }}>
        <div className="t">Permanently delete this Slack app?</div>
        <dl className="sl-kv">
          <dt>App name</dt><dd>{row.slack_name ?? name}</dd>
          <dt>App ID</dt><dd><span className="mono selectable">{appId}</span></dd>
          <dt>Workspace</dt><dd>{row.workspace_name ?? "Unknown"} <span className="mono">({row.slack_workspace})</span></dd>
        </dl>
        <div className="d">This can't be undone. Creating the app again gives a different App ID and bot identity. Your local files are backed up, but the backup can't restore the Slack app.</div>
        <input className="field" autoFocus value={typed} placeholder={`Type ${appId} to confirm`} aria-label="App ID to confirm"
          style={{ height: 34, marginTop: 8 }} onChange={(e) => setTyped(e.target.value.trim())} />
      </div>
      <div className="act">
        <button className="p-btn quiet sm" onClick={() => { setTyped(""); setStep("choose"); }}>Back</button>
        <button className="p-btn danger sm" disabled={busy || typed !== appId}
          onClick={() => void tags.remove(row, appId).then((ok) => done(ok, `Deleted the Slack app and removed ${name}.`))}>Delete app and remove</button>
      </div>
    </div>
  );
}

type FilterProps = { hideErrors: boolean; setHideErrors: (hide: boolean) => void };
type ActivityEntry = { tag: string; item: ActivityItem };
type HistoryProps = { hasMore: boolean; loading: boolean; loadOlder: () => void };
const activityKey = ({ tag, item }: ActivityEntry) => `${tag}:${item.run_id ?? item.at}`;
/** Older Tags report no thread, so each of their runs stays its own entry. */
const activityThread = (entry: ActivityEntry) => entry.item.thread ? `${entry.tag}:thread:${entry.item.thread}` : activityKey(entry);

function ActivityFilter({ hideErrors, setHideErrors }: FilterProps) {
  return <label className="activity-filter"><input type="checkbox" checked={!hideErrors}
    onChange={(event) => setHideErrors(!event.target.checked)} />Show errors</label>;
}

function ActivityFeed({ api, rows, entries, hideErrors, hasMore, loading, loadOlder, showReplyPlace = true, empty = "No activity yet", openRoutine }: Pick<FilterProps, "hideErrors"> & HistoryProps & {
  api: Bridge; rows: TagRow[]; entries: ActivityEntry[] | null; showReplyPlace?: boolean; empty?: string;
  /** Opens a routine from the label on a run it started. */
  openRoutine?: (id: string) => void;
}) {
  const now = new Date();
  const sorted = [...(entries ?? [])].filter(({ item }) => !hideErrors || item.kind !== "failed")
    .filter(({ item }) => item.kind !== "replied" || item.reply_summary?.trim() || item.reply_preview?.trim() || item.artifacts?.length)
    .sort((a, b) => new Date(a.item.at).getTime() - new Date(b.item.at).getTime());
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [openThreads, setOpenThreads] = useState<Record<string, boolean>>({});
  // Every reply in one Slack thread continues one conversation, so it is one entry.
  const threads = [...sorted.reduce((groups, entry) => {
    const group = groups.get(activityThread(entry)) ?? [];
    group.push(entry);
    return groups.set(activityThread(entry), group);
  }, new Map<string, ActivityEntry[]>()).values()]
    .sort((a, b) => new Date(a[a.length - 1].item.at).getTime() - new Date(b[b.length - 1].item.at).getTime());
  const feed = useRef<HTMLDivElement>(null);
  const viewport = useRef({ initialized: false, bottom: true, height: 0, first: "", top: 0 });
  // Older activity can join existing threads, so the first thread doesn't always change.
  const olderRequested = useRef(false);
  const requestOlder = useCallback(() => { olderRequested.current = true; loadOlder(); }, [loadOlder]);
  const first = threads[0] ? activityThread(threads[0][0]) : "";
  useLayoutEffect(() => {
    const scroller = feed.current?.closest<HTMLElement>(".sl-body");
    if (!scroller || entries === null) return;
    const previous = viewport.current;
    if (!previous.initialized || previous.bottom) scroller.scrollTop = scroller.scrollHeight;
    else if (previous.first !== first || olderRequested.current) scroller.scrollTop += scroller.scrollHeight - previous.height;
    if (!loading) olderRequested.current = false;
    viewport.current = { initialized: true, first, height: scroller.scrollHeight, top: scroller.scrollTop,
      bottom: scroller.scrollHeight - scroller.clientHeight - scroller.scrollTop < 40 };
  }, [entries, first, loading]);
  useEffect(() => {
    const scroller = feed.current?.closest<HTMLElement>(".sl-body");
    if (!scroller) return;
    const scroll = () => {
      const previous = viewport.current;
      const upwards = scroller.scrollTop < previous.top;
      viewport.current = { ...previous, top: scroller.scrollTop, height: scroller.scrollHeight,
        bottom: scroller.scrollHeight - scroller.clientHeight - scroller.scrollTop < 40 };
      if (upwards && scroller.scrollTop < 80 && hasMore && !loading) requestOlder();
    };
    scroller.addEventListener("scroll", scroll);
    return () => scroller.removeEventListener("scroll", scroll);
  }, [hasMore, loading, requestOlder]);
  const message = ({ tag, item }: ActivityEntry) => {
    const row = rows.find((row) => row.id === tag);
    if (!row) return null;
    const preview = item.reply_summary?.trim() || item.reply_preview?.trim();
    const at = new Date(item.at);
    const key = activityKey({ tag, item });
    return (
      <div className={item.kind === "working" ? "sl-msg working" : "sl-msg"}>
        {/* Only the request still running moves; past messages keep a still picture. */}
        <Avatar row={row} size={34} badge={false} className="" mood={item.kind === "working" ? "work" : "idle"} still={item.kind !== "working"} />
        <div>
          <div className="who">{title(row)}<span>{clock(at)}</span>{item.model && <span title={`${item.backend ?? "Agent"} · ${item.model}${item.reasoning_effort ? ` · ${effortLabel(item.reasoning_effort)} thinking` : ""}`}>· {item.model_name || item.model}{item.reasoning_effort && ` ${effortShort(item.reasoning_effort)}`}</span>}
            {item.duration_seconds != null && Number.isFinite(item.duration_seconds) && item.duration_seconds > 0 && <span title="Generation time, including tool work">· {generationTime(item.duration_seconds)}</span>}
            {item.usage && <ActivityTokens usage={item.usage} />}
            <StepsToggle item={item} open={!!expanded[key]} controls={`${key}:work`} onToggle={() => setExpanded((all) => ({ ...all, [key]: !all[key] }))} />
            {item.trigger && (openRoutine
              ? <button className="activity-routine" title="Open this routine" onClick={() => openRoutine(item.trigger!.routine_id)}><Icon name="clock" size={11} />Routine: {item.trigger.name}</button>
              : <span className="activity-routine"><Icon name="clock" size={11} />Routine: {item.trigger.name}</span>)}
          </div>
          {item.kind === "replied" ?
            preview && <p className="activity-reply-preview">{preview}</p>
            : <p>{ACTIVITY_TEXT[item.kind] ?? "Worked in"} <span className={item.dm ? undefined : "sl-chan"}>{placeText(item)}</span>{item.kind === "working" ? "…" : ""}</p>}
          <ActivityArtifacts api={api} item={item} />
          <ActivityDetails api={api} tag={row.id} item={item} open={!!expanded[key]} id={`${key}:work`}>
            {item.kind === "replied" && (showReplyPlace || (preview && !item.reply_summary?.trim())) &&
            <div className="activity-reply-place">
              {showReplyPlace && <>Replied{(item.dm || item.channel_name) && <> in <span className={item.dm ? undefined : "sl-chan"}>{placeText(item)}</span></>}</>}
              {preview && !item.reply_summary?.trim() && <span>{showReplyPlace && " · "}{item.reply_summary_status === "pending" ? "Summarizing…" : item.reply_summary_status === "unavailable" ? "Summary unavailable · Reply excerpt" : "Reply excerpt"}</span>}
            </div>
            }
          </ActivityDetails>
        </div>
      </div>
    );
  };
  let day = "";
  return <>
    <div ref={feed} className="activity-history">
    {hasMore && <button className="link activity-older" disabled={loading} onClick={requestOlder}>{loading ? "Loading older activity…" : "Load older activity"}</button>}
    {entries === null && <div className="sl-empty"><span className="spin" /></div>}
    {entries !== null && !sorted.length && <div className="sl-empty">
      <img src={tagIcon} alt="" style={{ width: 48, borderRadius: 12 }} />
      <div className="label">{empty}</div>
    </div>}
      {threads.map((thread) => {
        const latest = thread[thread.length - 1];
        const earlier = thread.slice(0, -1);
        const label = dayLabel(new Date(latest.item.at), now);
        const divider = label !== day;
        const group = activityThread(latest);
        day = label;
        return (
          <div key={group}>
            {divider && <div className="sl-day"><span>{label}</span></div>}
            <div className="activity-group">
            {message(latest)}
            {earlier.length > 0 && <div className="activity-thread-group">
              <button className="link activity-thread-toggle" aria-expanded={!!openThreads[group]}
                aria-label={`${openThreads[group] ? "Hide" : "Show"} ${earlier.length} earlier ${earlier.length === 1 ? "reply" : "replies"} in this thread`}
                onClick={() => setOpenThreads((all) => ({ ...all, [group]: !all[group] }))}>
                {openThreads[group] ? "Hide earlier" : `${earlier.length} earlier ${earlier.length === 1 ? "reply" : "replies"}`}
              </button>
              {openThreads[group] && earlier.map((entry) => <div key={activityKey(entry)}>{message(entry)}</div>)}
            </div>}
            </div>
          </div>
        );
      })}
    </div>
  </>;
}

/** The Tag's recent log lines; `find` marks the lines that mention it, such as a run's error reference. */
function Logs({ text, copyLog, find = "", clearFind }: { text: string; copyLog: () => Promise<boolean>; find?: string; clearFind?: () => void }) {
  const copy = useCopied();
  const box = useRef<HTMLPreElement>(null);
  const lines = text.trim() ? text.trim().split("\n") : [];
  const hits = find ? lines.filter((line) => line.includes(find)).length : 0;
  useEffect(() => {
    if (find) box.current?.querySelector("mark")?.scrollIntoView?.({ block: "center" });
  }, [find, text]);
  return <>
    <div className="row" style={{ marginBottom: 8 }}>
      <p className="lead" style={{ margin: 0, flex: 1 }}>Recent entries from this Tag's services, newest last.</p>
      <button className="p-btn soft sm" onClick={() => void copyLog().then((ok) => ok && copy.mark())}>{copy.label("Copy full log")}</button>
    </div>
    {find && <p className="meta log-find" role="status">
      {hits ? `${hits} ${hits === 1 ? "line mentions" : "lines mention"} ${find}.` : `No recent line mentions ${find}; it may be older than these entries.`}
      {clearFind && <> <button className="link" onClick={clearFind}>Show all</button></>}
    </p>}
    <pre ref={box} className="logbox selectable" aria-label="Tag log" style={{ maxHeight: "none" }}>
      {!lines.length ? "No log entries yet." : find
        ? lines.map((line, index) => <span key={index}>{line.includes(find) ? <mark>{line}</mark> : line}{"\n"}</span>)
        : text.trim()}
    </pre>
  </>;
}

function Activity({ api, row, items, showLogs, problem, openAI, hideErrors, hasMore, loading, loadOlder, openRoutine }: Pick<FilterProps, "hideErrors"> & HistoryProps & {
  api: Bridge; row: TagRow; items: ActivityItem[] | null; copyLog: () => Promise<boolean>; showLogs: () => void; problem: string | null; openAI: () => void;
  openRoutine?: (id: string) => void;
}) {
  const attention = status(row) === "attention";
  return <>
    <ActivityFeed api={api} rows={[row]} entries={items?.map((item) => ({ tag: row.id, item })) ?? null}
      hasMore={hasMore} loading={loading} loadOlder={loadOlder} hideErrors={hideErrors} openRoutine={openRoutine} />
      {problem && (
        <div className="notice" style={{ marginTop: 8 }}>
          <span className="ic"><Icon name="warn" size={16} /></span>
          <div style={{ flex: 1 }}><div className="t">Can't answer · {problem}</div><div className="d">Fix it in AI &amp; models.</div></div>
          <div className="act"><button className="p-btn soft sm" onClick={openAI}>Fix</button></div>
        </div>
      )}
      {attention && (
        <div className="notice" style={{ marginTop: 8 }}>
          <span className="ic"><Icon name="warn" size={16} /></span>
          <div style={{ flex: 1 }}><div className="t">{problemText(row)}</div><div className="d">{problemHelp(row, problem)}</div></div>
          <div className="act"><button className="p-btn soft sm" onClick={showLogs}>View logs</button></div>
        </div>
      )}
  </>;
}

const DESCRIPTION_LIMIT = 140;

function Details({ api, tags, row, copyLog, openAI, say, canDescribe, canRefreshModels }: {
  api: Bridge; tags: Tags; row: TagRow; copyLog: () => Promise<boolean>; openAI: () => void; say: (text: string) => void; canDescribe: boolean;
  canRefreshModels: boolean;
}) {
  const name = title(row);
  const choice = useModelChoice(api, row.id, {
    onSaved: (text) => { void tags.refresh(); if (text) say(`${name} ${text}`); },
  });
  const permissions = usePermissions(api, row.id, choice.report?.permissions, {
    onSaved: (text) => { if (text) say(`${name} ${text}`); },
    onRuleChanged: (action, rule) => say(`${{ allow: "Allowed", block: "Blocked", remove: "Removed" }[action]} ${rule} for ${name}. It applies from the next request.`),
  });
  const [renaming, setRenaming] = useState(false);
  const [newName, setNewName] = useState(row.slack_name ?? "");
  const copyMention = useCopied();
  const copyFullLog = useCopied();
  const folder = workingFolder(row as TagRow & { home?: string });
  const running = row.state === "running";
  const saved = choice.report?.default_model;
  // A failed rename or description change shows under the field being edited, which stays open to retry.
  const [renameError, setRenameError] = useState("");
  const rename = async () => {
    const failure = await tags.rename(row, newName);
    setRenameError(failure);
    if (!failure) { setRenaming(false); say(`Renamed to ${newName.trim()}`); }
  };
  const [describing, setDescribing] = useState(false);
  const [newDescription, setNewDescription] = useState(row.description ?? "");
  const [describeError, setDescribeError] = useState("");
  const describe = async () => {
    const failure = await tags.describe(row, newDescription);
    setDescribeError(failure);
    if (!failure) { setDescribing(false); say(newDescription.trim() ? "Saved the description" : "Cleared the description"); }
  };
  const describeBusy = tags.busy.has(row.id);
  return (
    <>
      <dl className="sl-kv">
        <dt>Name</dt>
        {renaming ? (
          <dd>
            <input className="field" autoFocus value={newName} aria-label="Name in Slack" maxLength={35} style={{ height: 32, flex: 1, minWidth: 0, maxWidth: 240 }}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && newName.trim()) void rename(); if (e.key === "Escape") { setRenaming(false); setRenameError(""); } }} />
            <button className="p-btn quiet sm" onClick={() => { setRenaming(false); setRenameError(""); }}>Cancel</button>
            <button className="p-btn ink sm" disabled={!newName.trim() || tags.busy.has(row.id)} onClick={() => void rename()}>Save</button>
            {renameError && <div className="edit-err"><ErrorLine>{renameError}</ErrorLine></div>}
          </dd>
        ) : (
          <dd>
            <span className="mention">@{name}</span>
            <button className="link" onClick={() => void api.copy(`@${name}`).then(() => { say("Copied mention"); copyMention.mark(); }, () => say("Could not copy the mention"))}>{copyMention.label("Copy")}</button>
            {row.slack_name && <button className="link" onClick={() => { setNewName(row.slack_name ?? ""); setRenaming(true); }}>Rename</button>}
          </dd>
        )}
        {(row.description || canDescribe) && <dt>Description</dt>}
        {describing ? (
          <dd className="desc-edit">
            <div className="desc-wrap">
              <textarea className="field desc" autoFocus rows={2} maxLength={DESCRIPTION_LIMIT} value={newDescription} aria-label="Description" ref={fitText}
                placeholder="One line on what it does" onChange={(e) => { setNewDescription(e.target.value.replace(/[\r\n]+/g, " ")); fitText(e.currentTarget); }}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void describe(); } if (e.key === "Escape") { setDescribing(false); setDescribeError(""); } }} />
              {newDescription.length > DESCRIPTION_LIMIT - 30 && <span className="desc-count">{DESCRIPTION_LIMIT - newDescription.length}</span>}
            </div>
            {describeError && <ErrorLine>{describeError}</ErrorLine>}
            <div className="desc-acts">
              <button className="p-btn quiet sm" onClick={() => { setDescribing(false); setDescribeError(""); }}>Cancel</button>
              <button className="p-btn ink sm" disabled={describeBusy || newDescription.trim() === (row.description ?? "")} onClick={() => void describe()}>{describeBusy ? "Saving…" : "Save"}</button>
            </div>
          </dd>
        ) : (row.description || canDescribe) && (
          <dd className="desc-dd">
            {row.description ? <span className="selectable">{row.description}</span> : <span className="meta">No description</span>}
            {canDescribe && row.slack_name && <button className="link" onClick={() => { setNewDescription(row.description ?? ""); setDescribing(true); }}>{row.description ? "Edit" : "Add"}</button>}
          </dd>
        )}
        <dt>Workspace</dt>
        <dd><WorkspaceMark label={row.workspace_name ?? ""} icon={row.workspace_icon ?? null} />{row.workspace_name ?? row.slack_workspace}</dd>
      </dl>
      <hr className="sl-sep" />
      <dl className="sl-kv">
        <dt>Model</dt>
        <dd className="mdd">
          {choice.report && !choice.report.usable.length ? (
            <span className="meta">Connect an AI account in Settings to choose a model. <button className="link" style={{ padding: 0 }} onClick={openAI}>Open Settings</button></span>
          ) : (
            <>
              <ModelMenu models={choice.models} report={choice.report} value={choice.value} onChange={choice.pick} below disabled={choice.save !== "idle" && choice.save !== "saved"}
                onRefresh={canRefreshModels ? () => void choice.refresh() : undefined} refreshing={choice.refreshing} refreshError={choice.refreshError} />
              <ModelWarning models={choice.models} value={choice.value} label={saved?.label ?? ""} tail="Pick another so new tasks don't fail." />
              <ThinkingRow entry={choice.entry} value={choice.level} onChange={choice.pickEffort} disabled={choice.save === "saving" || choice.save === "restarting"} />
              <p className="mcap">Applies to all requests to this Tag.</p>
              <SaveBar choice={choice} what={modelSaveText(choice)} tagName={name} running={running} />
            </>
          )}
          {choice.error && <ErrorLine>{choice.error}</ErrorLine>}
        </dd>
        {permissions.report && <>
          <dt>Permissions</dt>
          <dd className="mdd pdd">
            <PermissionsRow choice={permissions} using={saved?.backend} disabled={permissions.save === "saving" || permissions.save === "restarting"} openSettings={openAI} />
            <SaveBar choice={permissions} what="the new permissions" tagName={name} running={running} held={permissions.ruleBusy !== null} />
            {permissions.error && <ErrorLine>{permissions.error}</ErrorLine>}
          </dd>
        </>}
      </dl>
      <hr className="sl-sep" />
      <dl className="sl-kv">
        <dt>Working folder</dt>
        <dd><span className="mono selectable">{shortPath(folder)}</span><button className="link" onClick={() => void api.open(folder)}>Show</button></dd>
        <dt>Log</dt>
        <dd><span className="meta">For troubleshooting</span><button className="link" onClick={() => void copyLog().then((ok) => ok && copyFullLog.mark())}>{copyFullLog.label("Copy full log")}</button></dd>
      </dl>
      <hr className="sl-sep" />
      <RemoveTag tags={tags} row={row} say={say} />
    </>
  );
}
