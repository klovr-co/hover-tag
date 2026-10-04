// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Settings → AI & models: each Tag's agent connections, default model and
// thinking level. Changes a running Tag reads at start wait for a click.
import { useEffect, useReducer, useRef, useState } from "react";
import type { Bridge, Session } from "../lib/bridge";
import { idleSignIn, needsRestart, resultLine, signInArgs, signInReducer, type Connection } from "../lib/ai";
import { useModelChoice } from "../lib/model";
import { status as tagStatus, title, type TagRow } from "../lib/protocol";
import { failureLine, type Tags } from "../lib/tags";
import keyArt from "../assets/art/tag-key.png";
import { ChangeAccount, ConnectionRow, ModelMenu, ModelWarning, SaveBar, ThinkingRow, type RowAction } from "./AI";
import { Avatar, CompactSky, ErrorLine, Icon, Primary, Spinner, WorkspaceMark } from "./ui";

/** Tags that finished setup; the others have no agent settings yet. */
export const configuredTags = (rows: TagRow[]) => rows.filter((row) => row.valid && tagStatus(row) !== "setup");

const WORD = { online: "Online", offline: "Offline", setup: "Not in Slack yet", attention: "Stopped" };

export function AISettings({ api, tags, initial, close, add }: {
  api: Bridge; tags: Tags; initial?: string; close: () => void; add?: () => void;
}) {
  const choices = configuredTags(tags.rows);
  const fallback = choices.find((r) => r.main) ?? choices[0];
  const [tagId, setTagId] = useState(initial && choices.some((r) => r.id === initial) ? initial : fallback?.id ?? "");
  const row = choices.find((r) => r.id === tagId) ?? null;
  if (!row) {
    return (
      <>
        <CompactSky title="AI & models" sub="No Tags yet" back={close} />
        <div className="body"><div className="card empty">
          <img src={keyArt} alt="" style={{ width: 88 }} />
          <h3>No Tags yet</h3>
          <p>Each Tag has its own default model. Add a Tag first.</p>
          {add && <Primary title="Add Tag" icon="plus" onClick={add} />}
        </div></div>
      </>
    );
  }
  return <TagAI key={row.id} api={api} tags={tags} row={row} choices={choices} pick={setTagId} close={close} />;
}

function TagAI({ api, tags, row, choices, pick, close }: {
  api: Bridge; tags: Tags; row: TagRow; choices: TagRow[]; pick: (id: string) => void; close: () => void;
}) {
  const name = title(row);
  const [toast, setToast] = useState("");
  const choice = useModelChoice(api, row.id, {
    onSaved: (text) => { void tags.refresh(); if (text) setToast(`${name} ${text}`); },
  });
  const [menu, setMenu] = useState(false);
  const [checked, setChecked] = useState<"idle" | "checking" | "done">("idle");
  const [opened, setOpened] = useState(new Set<string>());
  const [changing, setChanging] = useState<Connection | null>(null);
  const [restartNeeded, setRestartNeeded] = useState(false);
  const [error, setError] = useState("");
  const [signIn, dispatch] = useReducer(signInReducer, idleSignIn);
  const session = useRef<Session | null>(null);
  const report = choice.report;
  const running = report?.running ?? row.state === "running";

  // A sign-in that's still running when the screen closes is cancelled, not left behind.
  useEffect(() => () => session.current?.stop(), []);

  const check = async () => {
    setChecked("checking");
    await choice.reload();
    setChecked("done");
  };

  const startSignIn = async (backend: string, options: { method?: string; resume?: boolean } = {}) => {
    if (!report) return;
    const connection = report.connections.find((c) => c.backend === backend);
    const restart = running && !!connection && (options.resume || needsRestart(connection, options.method));
    setChanging(null);
    setToast("");
    dispatch({ type: "start", backend });
    session.current = await api.setup(signInArgs(row.id, backend, { ...options, restart }),
      (line) => dispatch({ type: "line", line }),
      (code, stderr) => { dispatch({ type: "exit", code, stderr }); session.current = null; });
  };

  // When a sign-in ends, check again and say what changed.
  useEffect(() => {
    const result = signIn.result;
    if (!result) return;
    const agent = report?.connections.find((c) => c.backend === result.backend)?.name ?? "The agent";
    if (result.status === "connected") {
      setToast(result.restarted ? `${name} restarted with the new ${agent} account` : resultLine(result, agent));
      if (result.restart_required) setRestartNeeded(true);
      void choice.reload();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signIn.result]);

  const act = (action: RowAction) => {
    if (action.kind === "install" || action.kind === "update") {
      void api.open(action.url);
      setOpened((set) => new Set(set).add(action.backend));
    } else if (action.kind === "change_account") {
      setChanging(report?.connections.find((c) => c.backend === action.backend) ?? null);
    } else void startSignIn(action.backend, { resume: action.kind === "resume" });
  };

  const restartNow = async () => {
    const result = await api.tag([row.id, "restart"]);
    if (result.code !== 0) setError(failureLine(result, `Couldn't restart ${name}.`));
    else { setRestartNeeded(false); setToast(`${name} restarted`); }
    void tags.refresh();
  };

  const signingIn = !!signIn.step;
  const state = tagStatus(row);
  return (
    <>
      <CompactSky title="AI & models" sub={name} back={close} />
      <div className="body">
        <div className="section">
          <div className="card" style={{ position: "relative" }}>
            <button className="opt" aria-haspopup="listbox" aria-expanded={menu} aria-label={`Tag: ${name}`}
              style={{ borderRadius: "inherit" }} onClick={() => setMenu(!menu)}>
              <Avatar row={row} size={36} />
              <span className="txt"><span className="label">{name}</span>
                <span className="sub"><WorkspaceMark label={row.workspace_name ?? ""} icon={row.workspace_icon ?? null} />{row.workspace_name} · {WORD[state]}</span></span>
              <span className="caret" style={{ color: "var(--muted)", display: "grid" }}><Icon name="updown" /></span>
            </button>
            {menu && (
              <div className="menu" role="listbox" aria-label="Tags" style={{ left: 8, right: 8, top: "calc(100% + 4px)" }}>
                {choices.map((other) => (
                  <button key={other.id} role="option" aria-selected={other.id === row.id} onClick={() => { setMenu(false); pick(other.id); }}>
                    <Avatar row={other} size={22} badge={false} className="mi" />{title(other)}<span className="mws">{other.workspace_name}</span>
                    <span className="mck">{other.id === row.id && <Icon name="check" size={12} />}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="section">
          <div className="sec-head"><h3>Connections</h3><span className="spacer" />
            {checked === "done" && <span className="meta">Checked just now</span>}
            {checked === "checking" ? <span className="meta" style={{ display: "inline-flex", gap: 6, alignItems: "center" }}><Spinner small />Checking…</span>
              : <button className="p-btn soft sm" disabled={signingIn} onClick={() => void check()}>Check connections</button>}
          </div>
          <div className="card">
            {!report ? <div className="conn"><span className="cstat"><span className="spin" />Checking this Mac…</span></div>
              : report.connections.filter((c) => c.allowed !== false).map((connection) => (
                <ConnectionRow key={connection.backend} connection={connection} busy={signingIn || checked === "checking"}
                  signIn={signIn.backend === connection.backend ? signIn : null} tagName={name}
                  quiet={report.usable.length > 0 && !report.usable.includes(connection.backend)}
                  opened={opened.has(connection.backend)} act={act}
                  cancel={() => session.current?.send({ cancel: true })}
                  check={() => void check()} open={(url) => void api.open(url)} />
              ))}
          </div>
          {report && !report.usable.length && <p className="mcap">{name} can't answer in Slack until Codex or Claude is connected.</p>}
        </div>
        <div className="section">
          <div className="sec-head"><h3>Default model</h3></div>
          <div className="card mcard">
            {report && !report.usable.length ? <span className="meta">Connect Codex or Claude to choose a model.</span> : (
              <>
                <ModelMenu models={choice.models} report={report} value={choice.value} onChange={choice.pick}
                  disabled={choice.save === "saving" || choice.save === "restarting"} />
                {choice.models && <ModelWarning models={choice.models} value={choice.value} label={report?.default_model.label ?? ""} tail="Pick another so new tasks don't fail." />}
                {choice.models && <ThinkingRow entry={choice.entry} value={choice.level} onChange={choice.pickEffort}
                  disabled={choice.save === "saving" || choice.save === "restarting"} />}
                <p className="mcap">Also picks the agent. People's own choices in Slack are kept.</p>
              </>
            )}
          </div>
        </div>
        <SaveBar choice={choice} tagName={name} running={running} />
        {restartNeeded && !choice.dirty && (
          <div className="savebar">
            <span className="t">Restart {name} to use this change.</span>
            <button className="p-btn quiet sm" onClick={() => setRestartNeeded(false)}>Later</button>
            <button className="p-btn ink sm" onClick={() => void restartNow()}>Restart now</button>
          </div>
        )}
        {toast && <div className="savebar ok" role="status"><span className="t"><Icon name="check" size={12} />{toast}</span></div>}
        {(error || choice.error) && <ErrorLine>{error || choice.error}</ErrorLine>}
      </div>
      {changing && !signingIn && (
        <ChangeAccount connection={changing} tagName={name} running={running}
          cancel={() => setChanging(null)} choose={(method) => void startSignIn(changing.backend, { method })} />
      )}
    </>
  );
}
