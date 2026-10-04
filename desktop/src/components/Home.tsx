// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Every Tag on this computer, grouped by Slack workspace.
import { useState } from "react";
import type { Bridge } from "../lib/bridge";
import { groups, status, STATUS_LABEL, title, type Group, type TagRow } from "../lib/protocol";
import type { Tags } from "../lib/tags";
import { Avatar, ErrorLine, Icon, MoreMenu, Primary, Secondary, Switch, tagIcon, WorkspaceIcon } from "./ui";

const DOT = { online: "var(--green)", offline: "var(--secondary)", setup: "var(--orange)", attention: "var(--red)" };

/** The folder people open: the Tag's working folder, which holds its private `.tag` data. */
export function workingFolder(row: TagRow & { home?: string }) {
  const home = row.home ?? "";
  return home.endsWith(".tag") ? home.slice(0, -5).replace(/[\\/]$/, "") : `${home}/workspace`;
}

interface Props {
  api: Bridge;
  tags: Tags;
  add: () => void;
  finishSetup: (row: TagRow) => void;
  showLogs: (row: TagRow) => void;
  showSettings: () => void;
}

export function Home({ api, tags, add, finishSetup, showLogs, showSettings }: Props) {
  const online = tags.rows.filter((r) => r.state === "running").length;
  const [renaming, setRenaming] = useState<string | null>(null);
  return (
    <div className="stack gap-20">
      <div className="row gap-10">
        <img src={tagIcon} alt="" width={30} height={30} style={{ borderRadius: 7, imageRendering: "pixelated" }} />
        <div className="stack">
          <div className="title3" style={{ fontSize: 17, fontWeight: 700 }}>Tag</div>
          <div className="caption secondary">
            {tags.rows.length ? `${online} of ${tags.rows.length} online` : "No Tags yet"}
          </div>
        </div>
        <div className="spacer" />
        <button className="icon-btn" title="Settings" aria-label="Settings" onClick={showSettings}><Icon name="gear" /></button>
        <button className="icon-btn" title="Refresh" aria-label="Refresh" onClick={() => void tags.refresh()}><Icon name="refresh" /></button>
        <Primary title="Add Tag" icon="plus" onClick={add} />
      </div>
      {tags.loaded && tags.rows.length === 0 && (
        <div className="card stack gap-10" style={{ alignItems: "center", padding: "36px 20px", textAlign: "center" }}>
          <Avatar row={null} size={56} />
          <div className="headline">Bring your first Tag to Slack</div>
          <div className="secondary">Connect a workspace and Tag sets up its own Slack app.</div>
          <div style={{ marginTop: 4 }}><Primary title="Add Tag" icon="plus" onClick={add} /></div>
        </div>
      )}
      {groups(tags.rows).map((group) => (
        <div key={group.key} className="stack gap-8">
          <WorkspaceHeader group={group} tags={tags} />
          <div className="card" style={{ overflow: "visible" }}>
            {group.rows.map((row, index) => (
              <div key={row.id}>
                {index > 0 && <div className="divider" style={{ marginLeft: 62 }} />}
                <TagRowView api={api} row={row} tags={tags} renaming={renaming === row.id}
                  setRenaming={(on) => setRenaming(on ? row.id : null)}
                  finishSetup={() => finishSetup(row)} showLogs={() => showLogs(row)} />
              </div>
            ))}
          </div>
        </div>
      ))}
      {tags.error && <ErrorLine>{tags.error}</ErrorLine>}
    </div>
  );
}

function WorkspaceHeader({ group, tags }: { group: Group; tags: Tags }) {
  const running = group.rows.filter((r) => r.state === "running").length;
  const startable = group.rows.filter((r) => status(r) !== "setup").length;
  const all = running >= startable;
  return (
    <div className="row gap-8" style={{ padding: "0 4px" }}>
      <WorkspaceIcon path={group.icon} />
      <span className="headline" style={{ fontSize: 12 }}>{group.label}</span>
      <span className="count">{running}/{group.rows.length}</span>
      <div className="spacer" />
      {group.key && startable > 1 && (
        <button className="link-btn" disabled={tags.busy.has(group.key)}
          onClick={() => void tags.workspace(group.key, all ? "stop" : "start")}>
          <Icon name={all ? "stop" : "play"} size={11} />{all ? "Stop all" : "Start all"}
        </button>
      )}
    </div>
  );
}

interface RowProps {
  api: Bridge;
  row: TagRow;
  tags: Tags;
  renaming: boolean;
  setRenaming: (on: boolean) => void;
  finishSetup: () => void;
  showLogs: () => void;
}

function TagRowView({ api, row, tags, renaming, setRenaming, finishSetup, showLogs }: RowProps) {
  const busy = tags.busy.has(row.id);
  const state = status(row);
  const [name, setName] = useState(row.slack_name ?? "");
  const command = `tag ${row.nickname ?? row.id}`;
  const save = async () => { if (await tags.rename(row, name)) setRenaming(false); };
  const menu: [string, () => void][] = [
    ...(row.slack_name ? [["Rename…", () => { setName(row.slack_name ?? ""); setRenaming(true); }] as [string, () => void]] : []),
    ["Show logs", showLogs],
    ["Copy command", () => void api.copy(`${command} start`)],
    ["Show working folder", () => void api.open(workingFolder(row))],
  ];
  return (
    <div className="row gap-12" style={{ padding: "10px 12px" }} title={command}>
      <Avatar row={row} />
      {renaming ? (
        <>
          <input className="field" autoFocus placeholder="Name in Slack" value={name} aria-label="Name in Slack"
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && name.trim()) void save(); if (e.key === "Escape") setRenaming(false); }} />
          <Secondary title="Cancel" onClick={() => setRenaming(false)} />
          <Primary title="Save" disabled={busy || !name.trim()} onClick={() => void save()} />
        </>
      ) : (
        <>
          <div className="stack gap-4" style={{ minWidth: 0, flex: 1 }}>
            <div className="row gap-6">
              <span style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{title(row)}</span>
              {row.main && <span className="pill main" title="tag start without a name uses this Tag">Main</span>}
            </div>
            <div className="row gap-6 caption secondary">
              <span className="dot" style={{ background: DOT[state] }} />
              <span>{STATUS_LABEL[state]}</span>
              {row.nickname && <><span>·</span><span className="mono">tag {row.nickname}</span></>}
            </div>
          </div>
          <div className="row" style={{ width: 112, justifyContent: "flex-end" }}>
            {state === "setup" ? (
              <button className="finish" disabled={busy} onClick={finishSetup}>Finish setup</button>
            ) : (
              <Switch on={row.state === "running"} busy={busy} label={`${row.state === "running" ? "Stop" : "Start"} ${title(row)}`}
                onClick={() => void tags.toggle(row)} />
            )}
          </div>
          <MoreMenu label={`More for ${title(row)}`} items={menu} />
        </>
      )}
    </div>
  );
}
