// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Tag detail, laid out like Slack: workspaces in a rail, that workspace's Tags
// and channels in a sidebar, and the selected Tag's Activity, Channels and
// Details. It replaces the Logs screen and Home's ··· menu.
import { useCallback, useEffect, useState } from "react";
import type { Bridge } from "../lib/bridge";
import { effortShort, liveRows, placeText, type ActivityItem } from "../lib/home";
import { useModelChoice } from "../lib/model";
import { groups, parseJSON, problemText, status, title, type Group, type TagRow } from "../lib/protocol";
import type { Tags } from "../lib/tags";
import teamArt from "../assets/art/tag-team.png";
import keyArt from "../assets/art/tag-key.png";
import { workingFolder } from "./Home";
import { ModelMenu, ModelWarning, SaveBar, ThinkingRow } from "./AI";
import { Avatar, ErrorLine, Icon, Primary, Switch, workspaceColor, WorkspaceMark, source, tagIcon } from "./ui";

export type Selection = { kind: "tag"; id: string } | { kind: "channel"; id: string };
type Tab = "activity" | "channels" | "details";

interface Props {
  api: Bridge;
  tags: Tags;
  /** The Tag that was opened from Home. */
  initial: string;
  problems: Record<string, string | null>;
  back: () => void;
  add: () => void;
  showSettings: () => void;
  finishSetup: (row: TagRow) => void;
  openAI: (tag: string) => void;
  say: (text: string) => void;
}

const PRESENCE = { online: "var(--green)", offline: "transparent", setup: "var(--amber)", attention: "var(--red)" };
const WORD = { online: "Online", offline: "Offline", setup: "Not in Slack yet", attention: "Stopped" };

/** "~/Tag/maya" rather than the whole path. */
export const shortPath = (path: string) => path.replace(/^(\/Users\/[^/]+|\/home\/[^/]+|[A-Z]:\\Users\\[^\\]+)/, "~");

/** A channel's name for people, or its ID while Tag hasn't recorded one. */
const channelName = (channel: { id: string; name: string | null }) => channel.name ?? channel.id;

export function TagDetail({ api, tags, initial, problems, back, add, showSettings, finishSetup, openAI, say }: Props) {
  const rows = tags.rows;
  const start = rows.find((row) => row.id === initial);
  const [workspace, setWorkspace] = useState(start?.slack_workspace ?? "");
  const [selected, setSelected] = useState<Selection>({ kind: "tag", id: initial });
  const [tab, setTab] = useState<Tab>("activity");
  const [query, setQuery] = useState("");
  const all = groups(rows);
  const group = all.find((g) => g.key === workspace) ?? all[0];
  const top = (
    <div className="sl-top" data-tauri-drag-region>
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
            ? <ChannelPane api={api} channel={channels.find((c) => c.id === sel.id)!} rows={live} team={group.key} open={(id) => choose({ kind: "tag", id })} />
            : <TagPane key={sel.id} api={api} tags={tags} row={group.rows.find((r) => r.id === sel.id)!} tab={tab} setTab={setTab}
              problem={problems[sel.id] ?? null} finishSetup={finishSetup} openAI={openAI} say={say} />}
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

function ChannelPane({ api, channel, rows, team, open }: {
  api: Bridge; channel: { id: string; name: string | null }; rows: TagRow[]; team: string; open: (id: string) => void;
}) {
  const here = rows.filter((row) => (row.channels ?? []).some((c) => c.id === channel.id));
  return (
    <>
      <div className="sl-mhead">
        <span className="hash" style={{ width: 42, height: 42, fontSize: 20 }}>#</span>
        <div className="txt"><span className="name">{channelName(channel)}</span><span className="sub">{here.length === 1 ? "1 Tag answers here" : `${here.length} Tags answer here`}</span></div>
        <button className="p-btn soft sm" onClick={() => void api.open(slackChannel(team, channel.id))}>Open in Slack <Icon name="external" /></button>
      </div>
      <div className="sl-tabs" role="tablist"><button role="tab" aria-selected="true">Tags in this channel</button></div>
      <div className="sl-body">
        <div className="card">
          {here.map((row) => (
            <div key={row.id} className="r">
              <Avatar row={row} />
              <div className="txt"><span className="name"><span className="nm">{title(row)}</span></span><span className="sub">{WORD[status(row)]}</span></div>
              <button className="link" onClick={() => open(row.id)}>View</button>
            </div>
          ))}
        </div>
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

function TagPane({ api, tags, row, tab, setTab, problem, finishSetup, openAI, say }: {
  api: Bridge; tags: Tags; row: TagRow; tab: Tab; setTab: (tab: Tab) => void; problem: string | null;
  finishSetup: (row: TagRow) => void; openAI: (tag: string) => void; say: (text: string) => void;
}) {
  const state = status(row);
  const [activity, setActivity] = useState<ActivityItem[] | null>(null);
  const [logText, setLogText] = useState("");
  const load = useCallback(async () => {
    try {
      const result = await api.tag([row.id, "logs", "--json", "--limit", "200"]);
      if (result.code !== 0) { setActivity([]); return; }
      const logs = parseJSON<{ services: Record<string, string[]>; activity?: ActivityItem[] }>(result.stdout);
      setActivity(logs.activity ?? []);
      setLogText(Object.entries(logs.services).map(([name, lines]) => `── ${name} ──\n${lines.join("\n") || "No recent entries"}`).join("\n\n"));
    } catch {
      setActivity([]);
    }
  }, [api, row.id]);
  useEffect(() => {
    if (state === "setup") return;
    void load();
    const timer = setInterval(() => void load(), 30_000);
    return () => clearInterval(timer);
  }, [load, state]);

  if (state === "setup") {
    return (
      <div className="sl-empty">
        <img src={keyArt} alt="" style={{ width: 88, imageRendering: "pixelated" }} />
        <div className="h2">Finish setting up {title(row)}</div>
        <p className="lead">It isn't in Slack yet. Pick up where you left off: sign in, choose channels, and name it.</p>
        <Primary title="Continue" after="arrow" onClick={() => finishSetup(row)} />
      </div>
    );
  }
  const on = row.state === "running";
  const channels = row.channels ?? [];
  const model = row.default_model_name ?? "";
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
              {channels.length > 0 && ` · in ${channels.map((c) => `#${channelName(c)}`).join(", ")}`}</span>
          </span>
        </div>
        <Switch on={on} busy={tags.busy.has(row.id)} label={`${on ? "Stop" : "Start"} ${title(row)}`} onClick={() => void tags.toggle(row)} />
      </div>
      <div className="sl-tabs" role="tablist">
        {([["activity", "Activity"], ["channels", `Channels ${channels.length}`], ["details", "Details"]] as const).map(([key, label]) => (
          <button key={key} role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>{label}</button>
        ))}
      </div>
      <div className="sl-body" role="tabpanel">
        {tab === "activity" && <Activity row={row} items={activity} copyLog={copyLog} problem={problem} openAI={() => openAI(row.id)} />}
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
        {tab === "details" && <Details api={api} tags={tags} row={row} copyLog={copyLog} openAI={() => openAI(row.id)} say={say} />}
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

function Activity({ row, items, copyLog, problem, openAI }: {
  row: TagRow; items: ActivityItem[] | null; copyLog: () => void; problem: string | null; openAI: () => void;
}) {
  const now = new Date();
  const attention = status(row) === "attention";
  const sorted = [...(items ?? [])].reverse();
  let day = "";
  return (
    <>
      {items === null && <div className="sl-empty"><span className="spin" /></div>}
      {items !== null && !items.length && !attention && (
        <div className="sl-empty">
          <img src={tagIcon} alt="" style={{ width: 48, borderRadius: 12 }} />
          <div className="label">No activity yet</div>
          <span>Mention <span className="mention">@{title(row)}</span> in Slack. Its replies show up here.</span>
        </div>
      )}
      {sorted.map((item, i) => {
        const at = new Date(item.at);
        const label = dayLabel(at, now);
        const divider = label !== day;
        day = label;
        return (
          <div key={`${item.at}-${i}`}>
            {divider && <div className="sl-day"><span>{label}</span></div>}
            <div className="sl-msg">
              <Avatar row={row} size={34} badge={false} className="" />
              <div>
                <div className="who">{title(row)}<span>{clock(at)}</span></div>
                <p>{ACTIVITY_TEXT[item.kind] ?? "Worked in"} <span className={item.dm ? undefined : "sl-chan"}>{placeText(item)}</span>{item.kind === "working" ? "…" : ""}</p>
              </div>
            </div>
          </div>
        );
      })}
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
          <div style={{ flex: 1 }}><div className="t">{problemText(row)}</div><div className="d">Switch it on to try again, or open the full log.</div></div>
          <div className="act"><button className="p-btn soft sm" onClick={copyLog}>Copy full log</button></div>
        </div>
      )}
    </>
  );
}

function Details({ api, tags, row, copyLog, openAI, say }: {
  api: Bridge; tags: Tags; row: TagRow; copyLog: () => void; openAI: () => void; say: (text: string) => void;
}) {
  const name = title(row);
  const choice = useModelChoice(api, row.id, {
    onSaved: (text) => { void tags.refresh(); if (text) say(`${name} ${text}`); },
  });
  const [renaming, setRenaming] = useState(false);
  const [newName, setNewName] = useState(row.slack_name ?? "");
  const command = `tag ${row.nickname ?? row.id}`;
  const folder = workingFolder(row as TagRow & { home?: string });
  const running = row.state === "running";
  const saved = choice.report?.default_model;
  const rename = async () => { if (await tags.rename(row, newName)) { setRenaming(false); say(`Renamed to ${newName.trim()}`); } };
  return (
    <>
      <dl className="sl-kv">
        <dt>Model</dt>
        <dd className="mdd">
          {choice.report && !choice.report.usable.length ? (
            <span className="meta">Connect Codex or Claude to choose a model. <button className="link" style={{ padding: 0 }} onClick={openAI}>Connections</button></span>
          ) : (
            <>
              <ModelMenu models={choice.models} report={choice.report} value={choice.value} onChange={choice.pick} below disabled={choice.save !== "idle" && choice.save !== "saved"} />
              <ModelWarning models={choice.models} value={choice.value} label={saved?.label ?? ""} tail="Pick another so new tasks don't fail." />
              <ThinkingRow entry={choice.entry} value={choice.level} onChange={choice.pickEffort} disabled={choice.save === "saving" || choice.save === "restarting"} />
              <p className="mcap">Also picks the agent. People's own choices in Slack are kept. <button className="link" onClick={openAI}>Connections</button></p>
              <SaveBar choice={choice} tagName={name} running={running} />
            </>
          )}
          {choice.error && <ErrorLine>{choice.error}</ErrorLine>}
        </dd>
        <dt>Mention</dt>
        <dd><span className="mention">@{name}</span><button className="link" onClick={() => { void api.copy(`@${name}`); say("Copied mention"); }}>Copy</button></dd>
        <dt>Workspace</dt>
        <dd><WorkspaceMark label={row.workspace_name ?? ""} icon={row.workspace_icon ?? null} />{row.workspace_name ?? row.slack_workspace}</dd>
        <dt>Terminal</dt>
        <dd><span className="mono">{command}</span><button className="link" onClick={() => { void api.copy(command); say("Copied command"); }}>Copy</button></dd>
        <dt>Working folder</dt>
        <dd><span className="mono selectable">{shortPath(folder)}</span><button className="link" onClick={() => void api.open(folder)}>Show</button></dd>
        {row.main && <><dt>Main Tag</dt><dd>Used when you run <span className="mono">tag start</span> without a name</dd></>}
      </dl>
      {renaming ? (
        <div className="foot" style={{ marginTop: 8 }}>
          <input className="field" autoFocus value={newName} aria-label="Name in Slack" maxLength={35} style={{ height: 34, maxWidth: 260 }}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && newName.trim()) void rename(); if (e.key === "Escape") setRenaming(false); }} />
          <button className="p-btn quiet sm" onClick={() => setRenaming(false)}>Cancel</button>
          <button className="p-btn ink sm" disabled={!newName.trim() || tags.busy.has(row.id)} onClick={() => void rename()}>Save</button>
        </div>
      ) : (
        <div className="foot" style={{ marginTop: 8 }}>
          {row.slack_name && <button className="p-btn soft sm" onClick={() => { setNewName(row.slack_name ?? ""); setRenaming(true); }}>Rename…</button>}
          <button className="p-btn soft sm" onClick={copyLog}>Copy full log</button>
        </div>
      )}
    </>
  );
}
