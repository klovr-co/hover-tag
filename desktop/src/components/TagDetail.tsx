// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Tag detail, laid out like Slack: workspaces in a rail, that workspace's Tags
// and channels in a sidebar, and the selected Tag's Activity, Channels and
// Details. It replaces the Logs screen and Home's ··· menu.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Bridge } from "../lib/bridge";
import { effortLabel, effortShort, generationTime, liveRows, modelText, placeText, type ActivityItem } from "../lib/home";
import { ActivityDetails, StepsToggle } from "./ActivityDetails";
import { ActivityArtifacts } from "./ActivityArtifacts";
import { ActivityTokens } from "./ActivityTokens";
import { useModelChoice } from "../lib/model";
import { collapseRepeats, groups, parseJSON, problemHelp, problemText, status, title, type Group, type TagRow } from "../lib/protocol";
import type { Tags } from "../lib/tags";
import teamArt from "../assets/art/tag-team.png";
import keyArt from "../assets/art/tag-key.png";
import { workingFolder } from "./Home";
import { ModelMenu, ModelWarning, SaveBar, ThinkingRow } from "./AI";
import { Avatar, dragWindow, ErrorLine, fitText, Icon, Primary, Switch, workspaceColor, WorkspaceMark, source, tagIcon } from "./ui";

export type Selection = { kind: "tag"; id: string } | { kind: "channel"; id: string };
export type Tab = "activity" | "channels" | "logs" | "details";

interface Props {
  api: Bridge;
  tags: Tags;
  /** The Tag that was opened from Home. */
  initial: string;
  /** The tab to show first; a Slack link opens Details. */
  initialTab?: Tab;
  problems: Record<string, string | null>;
  back: () => void;
  add: () => void;
  showSettings: () => void;
  finishSetup: (row: TagRow) => void;
  openAI: (tag: string) => void;
  say: (text: string) => void;
  /** The installed Tag can change descriptions (`describe` capability). */
  canDescribe?: boolean;
}

const PRESENCE = { online: "var(--green)", offline: "transparent", setup: "var(--amber)", attention: "var(--red)" };
const WORD = { online: "Online", offline: "Offline", setup: "Not in Slack yet", attention: "Stopped" };

/** "~/Tag/maya" rather than the whole path. */
export const shortPath = (path: string) => path.replace(/^(\/Users\/[^/]+|\/home\/[^/]+|[A-Z]:\\Users\\[^\\]+)/, "~");

/** A channel's name for people, or its ID while Tag hasn't recorded one. */
const channelName = (channel: { id: string; name: string | null }) => channel.name ?? channel.id;

export function TagDetail({ api, tags, initial, initialTab = "activity", problems, back, add, showSettings, finishSetup, openAI, say, canDescribe = false }: Props) {
  const rows = tags.rows;
  const start = rows.find((row) => row.id === initial);
  const [workspace, setWorkspace] = useState(start?.slack_workspace ?? "");
  const [selected, setSelected] = useState<Selection>({ kind: "tag", id: initial });
  const [tab, setTab] = useState<Tab>(initialTab);
  const [query, setQuery] = useState("");
  const [hideErrors, setHideErrors] = useState(true);
  const all = groups(rows);
  const group = all.find((g) => g.key === workspace) ?? all[0];
  const top = (
    <div className="sl-top" onMouseDown={dragWindow}>
      <button className="sky-btn" onClick={back} aria-label="Back to Your Tags"><Icon name="left" />Your Tags</button>
      <label className="sl-search"><Icon name="search" size={16} />
        <input placeholder="Search Tags and channels" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search Tags and channels" />
      </label>
      <button className="sky-btn" aria-label="Settings" title="Settings" onClick={showSettings}><Icon name="gear" size={16} /></button>
      <button className="add" onClick={add}><Icon name="plus" />Add Tag</button>
    </div>
  );
  if (!group) {
    return (
      <>
        {top}
        <div className="sl-grid" style={{ gridTemplateColumns: "64px 1fr" }}>
          <div className="sl-rail"><button className="sl-ws plus" aria-label="Add Tag" onClick={add}><Icon name="plus" /></button></div>
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
  const live = liveRows(group.rows);
  const setup = group.rows.filter((row) => status(row) === "setup");
  const channels = channelsOf(live);
  const q = query.trim().toLocaleLowerCase().replace(/^#/, "");
  const match = (text: string) => !q || text.toLocaleLowerCase().includes(q);
  // Keep the selection inside this workspace.
  let sel = selected;
  if (sel.kind === "tag" && !group.rows.some((row) => row.id === sel.id)) sel = { kind: "tag", id: (live[0] ?? group.rows[0]).id };
  if (sel.kind === "channel" && !channels.some((c) => c.id === sel.id)) sel = { kind: "tag", id: (live[0] ?? group.rows[0]).id };
  const running = live.filter((row) => row.state === "running").length;
  const allOn = live.length > 0 && running >= live.length;
  const choose = (next: Selection) => { setSelected(next); setTab("activity"); };
  const item = (row: TagRow) => {
    const state = status(row);
    return (
      <button key={row.id} className={sel.kind === "tag" && sel.id === row.id ? "sl-item on" : "sl-item"} onClick={() => choose({ kind: "tag", id: row.id })}>
        <Avatar row={row} size={22} badge={false} className="" />
        <span className="nm">{title(row)}</span>
        <span className="pd" style={{ background: PRESENCE[state], boxShadow: state === "offline" ? "inset 0 0 0 1.5px var(--faint)" : undefined }} />
      </button>
    );
  };
  return (
    <>
      {top}
      <div className="sl-grid">
        <div className="sl-rail">
          {all.map((g) => <RailButton key={g.key} group={g} on={g === group} pick={() => { setWorkspace(g.key); setSelected({ kind: "tag", id: "" }); setTab("activity"); }} />)}
          <button className="sl-ws plus" aria-label="Add Tag" title="Add Tag" onClick={add}><Icon name="plus" /></button>
        </div>
        <div className="sl-side">
          <div className="sl-wshead"><b>{group.label}</b><span className="sl-meta">{live.length ? `${running} of ${live.length} online` : "Not connected yet"}</span></div>
          <div className="sl-list">
            {live.some((row) => match(title(row))) && <><div className="sl-sec">Tags</div>{live.filter((row) => match(title(row))).map(item)}</>}
            {setup.some((row) => match(title(row))) && <><div className="sl-sec" style={{ color: "var(--amber)" }}>Finish setting up</div>{setup.filter((row) => match(title(row))).map(item)}</>}
            {channels.some((c) => match(channelName(c))) && <><div className="sl-sec">Channels</div>
              {channels.filter((c) => match(channelName(c))).map((c) => (
                <button key={c.id} className={sel.kind === "channel" && sel.id === c.id ? "sl-item on" : "sl-item"} onClick={() => choose({ kind: "channel", id: c.id })}>
                  <span className="hs">#</span><span className="nm">{channelName(c)}</span>
                </button>
              ))}</>}
          </div>
          {live.length > 1 && group.key && (
            <div className="sl-foot">
              <button className="link" disabled={tags.busy.has(group.key)} onClick={() => void tags.workspace(group.key, allOn ? "stop" : "start")}>
                <Icon name={allOn ? "stop" : "play"} size={10} />{allOn ? "Stop all" : "Start all"}
              </button>
            </div>
          )}
        </div>
        <div className="sl-main">
          {sel.kind === "channel"
            ? <ChannelPane key={`${group.key}:${sel.id}:${hideErrors}`} hideErrors={hideErrors} setHideErrors={setHideErrors} api={api} channel={channels.find((c) => c.id === sel.id)!} rows={live} team={group.key} open={(id) => choose({ kind: "tag", id })} />
            : <TagPane key={`${sel.id}:${hideErrors}`} hideErrors={hideErrors} setHideErrors={setHideErrors} api={api} tags={tags} row={group.rows.find((r) => r.id === sel.id)!} tab={tab} setTab={setTab}
              problem={problems[sel.id] ?? null} finishSetup={finishSetup} openAI={openAI} say={say} canDescribe={canDescribe} />}
        </div>
      </div>
    </>
  );
}

function RailButton({ group, on, pick }: { group: Group; on: boolean; pick: () => void }) {
  const [failed, setFailed] = useState(false);
  const picture = !failed ? source(group.icon) : null;
  const unfinished = group.rows.filter((row) => status(row) === "setup").length;
  return (
    <button className={on ? "sl-ws on" : "sl-ws"} style={{ background: group.key ? workspaceColor(group.label) : "var(--faint)" }}
      title={group.label} aria-label={group.label} aria-pressed={on} onClick={pick}>
      {picture ? <img src={picture} alt="" onError={() => setFailed(true)} /> : (group.label[0] ?? "?").toUpperCase()}
      {unfinished > 0 && <span className="dotn">{unfinished}</span>}
    </button>
  );
}

/** Every channel the workspace's Tags answer in, by name. */
function channelsOf(rows: TagRow[]) {
  const seen = new Map<string, { id: string; name: string | null }>();
  for (const row of rows) for (const c of row.channels ?? []) if (!seen.has(c.id)) seen.set(c.id, c);
  return [...seen.values()].sort((a, b) => channelName(a).localeCompare(channelName(b)));
}

const slackChannel = (team: string, id: string) => `slack://channel?team=${encodeURIComponent(team)}&id=${encodeURIComponent(id)}`;

function ChannelPane({ api, channel, rows, team, open, hideErrors, setHideErrors }: FilterProps & {
  api: Bridge; channel: { id: string; name: string | null }; rows: TagRow[]; team: string; open: (id: string) => void;
}) {
  const here = rows.filter((row) => (row.channels ?? []).some((c) => c.id === channel.id));
  const [tab, setTab] = useState<"activity" | "tags">("activity");
  const [entries, setEntries] = useState<ActivityEntry[] | null>(null);
  const [failedTags, setFailedTags] = useState<string[]>([]);
  const [limit, setLimit] = useState(50);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  // Read every Tag in this workspace, including historical activity after it left the channel.
  const rowIds = JSON.stringify(rows.map((row) => row.id));
  useEffect(() => {
    if (tab !== "activity") return;
    let cancelled = false;
    let loading = false;
    const load = async () => {
      if (loading) return;
      loading = true;
      setLoading(true);
      const results = await Promise.allSettled((JSON.parse(rowIds) as string[]).map(async (id) => {
        const result = await api.tag([id, "logs", "--json", "--activity-channel", channel.id, "--activity-limit", String(limit), ...(hideErrors ? ["--hide-errors"] : [])]);
        if (result.code !== 0) throw new Error("Activity unavailable");
        const data = parseJSON<{ activity?: ActivityItem[]; activity_has_more?: boolean }>(result.stdout);
        return { entries: (data.activity ?? []).filter((item) => item.channel === channel.id).map((item) => ({ tag: id, item })), more: data.activity_has_more === true };
      }));
      if (!cancelled) {
        const combined = results.flatMap((result) => result.status === "fulfilled" ? result.value.entries : [])
          .sort((a, b) => new Date(b.item.at).getTime() - new Date(a.item.at).getTime());
        setEntries((previous) => mergeActivity(previous, combined.slice(0, limit)));
        setHasMore(combined.length > limit || results.some((result) => result.status === "fulfilled" && result.value.more));
        setLoading(false);
        const ids = JSON.parse(rowIds) as string[];
        setFailedTags(results.flatMap((result, index) => result.status === "rejected" ? [ids[index]] : []));
      }
      loading = false;
    };
    void load();
    const timer = setInterval(() => void load(), 30_000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [api, rowIds, channel.id, hideErrors, tab, limit]);
  return (
    <>
      <div className="sl-mhead">
        <span className="hash" style={{ width: 42, height: 42, fontSize: 20 }}>#</span>
        <div className="txt"><span className="name">{channelName(channel)}</span><span className="sub">{here.length === 1 ? "1 Tag answers here" : `${here.length} Tags answer here`}</span></div>
        <button className="p-btn soft sm" onClick={() => void api.open(slackChannel(team, channel.id))}>Open in Slack <Icon name="external" /></button>
      </div>
      <div className="sl-tabbar">
      <div className="sl-tabs" role="tablist">
        <button role="tab" aria-selected={tab === "activity"} onClick={() => setTab("activity")}>Activity</button>
        <button role="tab" aria-selected={tab === "tags"} onClick={() => setTab("tags")}>Tags in this channel</button>
      </div>
      {tab === "activity" && <ActivityFilter hideErrors={hideErrors} setHideErrors={setHideErrors} />}
      </div>
      <div key={tab} className="sl-body" role="tabpanel">
        {tab === "activity" ? <>
          {failedTags.length > 0 && <ErrorLine>{`Couldn't load activity for ${failedTags.map((id) => { const row = rows.find((row) => row.id === id); return row ? title(row) : id; }).join(", ")}. Retrying automatically.`}</ErrorLine>}
          <ActivityFeed api={api} rows={rows} entries={entries} showReplyPlace={false} hasMore={hasMore} loading={loading} loadOlder={() => setLimit((value) => value + 50)} hideErrors={hideErrors}
            empty={failedTags.length ? "Some activity is unavailable" : `No activity in #${channelName(channel)} yet`} />
        </> : <div className="card">
          {here.map((row) => (
            <div key={row.id} className="r">
              <Avatar row={row} />
              <div className="txt"><span className="name"><span className="nm">{title(row)}</span></span><span className="sub">{WORD[status(row)]}</span></div>
              <button className="link" onClick={() => open(row.id)}>View</button>
            </div>
          ))}
        </div>}
      </div>
    </>
  );
}

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

function TagPane({ api, tags, row, tab, setTab, problem, finishSetup, openAI, say, canDescribe, hideErrors, setHideErrors }: FilterProps & {
  api: Bridge; tags: Tags; row: TagRow; tab: Tab; setTab: (tab: Tab) => void; problem: string | null;
  finishSetup: (row: TagRow) => void; openAI: (tag: string) => void; say: (text: string) => void; canDescribe: boolean;
}) {
  const state = status(row);
  const [activity, setActivity] = useState<ActivityItem[] | null>(null);
  const [limit, setLimit] = useState(50);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [logText, setLogText] = useState("");
  const [logView, setLogView] = useState("");
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
  const copyLog = () => { void api.copy(logText); say("Copied the full log"); };
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
        <Switch on={on} busy={tags.busy.has(row.id)} label={`${on ? "Stop" : "Start"} ${title(row)}`} onClick={() => void tags.toggle(row)} />
      </div>
      <div className="sl-tabbar">
      <div className="sl-tabs" role="tablist">
        {([["activity", "Activity", null], ["channels", "Channels", channels.length], ["logs", "Logs", null], ["details", "Details", null]] as const).map(([key, label, count]) => (
          <button key={key} role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>
            {label}{count !== null && <> <span className="tcount">{count}</span></>}
          </button>
        ))}
      </div>
      {tab === "activity" && <ActivityFilter hideErrors={hideErrors} setHideErrors={setHideErrors} />}
      </div>
      <div key={tab} className="sl-body" role="tabpanel">
        {/* Rename, description, remove and start/stop report failures here, not only on Home. */}
        {tags.error && <ErrorLine>{tags.error}</ErrorLine>}
        {tab === "activity" && <Activity hasMore={hasMore} loading={loading} loadOlder={() => setLimit((value) => value + 50)} hideErrors={hideErrors} api={api} row={row} items={activity} copyLog={copyLog} showLogs={() => setTab("logs")} problem={problem} openAI={() => openAI(row.id)} />}
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
        {tab === "logs" && <Logs text={logView} copyLog={copyLog} />}
        {tab === "details" && <Details api={api} tags={tags} row={row} copyLog={copyLog} openAI={() => openAI(row.id)} say={say} canDescribe={canDescribe} />}
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
      <div className="notice" style={{ marginTop: 8 }}>
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
    <div className="notice" style={{ marginTop: 8 }}>
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
        <button className="p-btn soft sm" disabled={busy || typed !== appId}
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
const threadKey = (entry: ActivityEntry) => entry.item.thread ? `${entry.tag}:thread:${entry.item.thread}` : activityKey(entry);
function mergeActivity(previous: ActivityEntry[] | null, incoming: ActivityEntry[]) {
  const merged = new Map((previous ?? []).map((entry) => [activityKey(entry), entry]));
  for (const entry of incoming) merged.set(activityKey(entry), entry);
  return [...merged.values()];
}

function ActivityFilter({ hideErrors, setHideErrors }: FilterProps) {
  return <label className="activity-filter"><input type="checkbox" checked={!hideErrors}
    onChange={(event) => setHideErrors(!event.target.checked)} />Show errors</label>;
}

function ActivityFeed({ api, rows, entries, hideErrors, hasMore, loading, loadOlder, showReplyPlace = true, empty = "No activity yet" }: Pick<FilterProps, "hideErrors"> & HistoryProps & {
  api: Bridge; rows: TagRow[]; entries: ActivityEntry[] | null; showReplyPlace?: boolean; empty?: string;
}) {
  const now = new Date();
  const sorted = [...(entries ?? [])].filter(({ item }) => !hideErrors || item.kind !== "failed")
    .filter(({ item }) => item.kind !== "replied" || item.reply_summary?.trim() || item.reply_preview?.trim() || item.artifacts?.length)
    .sort((a, b) => new Date(a.item.at).getTime() - new Date(b.item.at).getTime());
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [openThreads, setOpenThreads] = useState<Record<string, boolean>>({});
  // Every reply in one Slack thread continues one conversation, so it is one entry.
  const threads = [...sorted.reduce((groups, entry) => {
    const group = groups.get(threadKey(entry)) ?? [];
    group.push(entry);
    return groups.set(threadKey(entry), group);
  }, new Map<string, ActivityEntry[]>()).values()]
    .sort((a, b) => new Date(a[a.length - 1].item.at).getTime() - new Date(b[b.length - 1].item.at).getTime());
  const feed = useRef<HTMLDivElement>(null);
  const viewport = useRef({ initialized: false, bottom: true, height: 0, first: "", top: 0 });
  // Older activity can join existing threads, so the first thread doesn't always change.
  const olderRequested = useRef(false);
  const requestOlder = useCallback(() => { olderRequested.current = true; loadOlder(); }, [loadOlder]);
  const first = threads[0] ? threadKey(threads[0][0]) : "";
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
      <div className="sl-msg">
        <Avatar row={row} size={34} badge={false} className="" />
        <div>
          <div className="who">{title(row)}<span>{clock(at)}</span>{item.model && <span title={`${item.backend ?? "Agent"} · ${item.model}${item.reasoning_effort ? ` · ${effortLabel(item.reasoning_effort)} thinking` : ""}`}>· {item.model_name || item.model}{item.reasoning_effort && ` ${effortShort(item.reasoning_effort)}`}</span>}
            {item.duration_seconds != null && Number.isFinite(item.duration_seconds) && item.duration_seconds > 0 && <span title="Generation time, including tool work">· {generationTime(item.duration_seconds)}</span>}
            {item.usage && <ActivityTokens usage={item.usage} />}
            <StepsToggle item={item} open={!!expanded[key]} controls={`${key}:work`} onToggle={() => setExpanded((all) => ({ ...all, [key]: !all[key] }))} />
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
        const group = threadKey(latest);
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

function Logs({ text, copyLog }: { text: string; copyLog: () => void }) {
  return <>
    <div className="row" style={{ marginBottom: 8 }}>
      <p className="lead" style={{ margin: 0, flex: 1 }}>Recent entries from this Tag's services, newest last.</p>
      <button className="p-btn soft sm" onClick={copyLog}>Copy full log</button>
    </div>
    <pre className="logbox selectable" aria-label="Tag log" style={{ maxHeight: "none" }}>{text.trim() || "No log entries yet."}</pre>
  </>;
}

function Activity({ api, row, items, showLogs, problem, openAI, hideErrors, hasMore, loading, loadOlder }: Pick<FilterProps, "hideErrors"> & HistoryProps & {
  api: Bridge; row: TagRow; items: ActivityItem[] | null; copyLog: () => void; showLogs: () => void; problem: string | null; openAI: () => void;
}) {
  const attention = status(row) === "attention";
  return <>
    <ActivityFeed api={api} rows={[row]} entries={items?.map((item) => ({ tag: row.id, item })) ?? null}
      hasMore={hasMore} loading={loading} loadOlder={loadOlder} hideErrors={hideErrors} />
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

function Details({ api, tags, row, copyLog, openAI, say, canDescribe }: {
  api: Bridge; tags: Tags; row: TagRow; copyLog: () => void; openAI: () => void; say: (text: string) => void; canDescribe: boolean;
}) {
  const name = title(row);
  const choice = useModelChoice(api, row.id, {
    onSaved: (text) => { void tags.refresh(); if (text) say(`${name} ${text}`); },
  });
  const [renaming, setRenaming] = useState(false);
  const [newName, setNewName] = useState(row.slack_name ?? "");
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
            <button className="link" onClick={() => { void api.copy(`@${name}`); say("Copied mention"); }}>Copy</button>
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
              <ModelMenu models={choice.models} report={choice.report} value={choice.value} onChange={choice.pick} below disabled={choice.save !== "idle" && choice.save !== "saved"} />
              <ModelWarning models={choice.models} value={choice.value} label={saved?.label ?? ""} tail="Pick another so new tasks don't fail." />
              <ThinkingRow entry={choice.entry} value={choice.level} onChange={choice.pickEffort} disabled={choice.save === "saving" || choice.save === "restarting"} />
              <p className="mcap">Applies to all requests to this Tag.</p>
              <SaveBar choice={choice} tagName={name} running={running} />
            </>
          )}
          {choice.error && <ErrorLine>{choice.error}</ErrorLine>}
        </dd>
      </dl>
      <hr className="sl-sep" />
      <dl className="sl-kv">
        <dt>Working folder</dt>
        <dd><span className="mono selectable">{shortPath(folder)}</span><button className="link" onClick={() => void api.open(folder)}>Show</button></dd>
        <dt>Log</dt>
        <dd><span className="meta">For troubleshooting</span><button className="link" onClick={copyLog}>Copy full log</button></dd>
      </dl>
      <hr className="sl-sep" />
      <RemoveTag tags={tags} row={row} say={say} />
    </>
  );
}
