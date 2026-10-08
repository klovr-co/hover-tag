// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Settings → AI connections: shared accounts for every Tag.
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { Bridge, Session } from "../lib/bridge";
import { idleSignIn, parseConnections, resultLine, signInArgs, signInReducer, type ApiGroup, groupApis, type Connection, type AIConnections } from "../lib/ai";
import type { Tags } from "../lib/tags";
import { failureLine } from "../lib/tags";
import { title } from "../lib/protocol";
import { ChangeAccount, ConnectionRow, type RowAction } from "./AI";
import { ApiDialog, ApiList } from "./ApiConnections";
import { CompactSky, ErrorLine, Icon, Spinner } from "./ui";
import { RiverFoot } from "./Settings";

/** Installation-wide accounts. Models belong to each Tag's Details tab. */
export function AISettings({ api, tags, close, apiConnections = false }: {
  api: Bridge; tags: Tags; close: () => void;
  /** The installed Tag supports `settings ai api` (the api-connections capability). */
  apiConnections?: boolean;
}) {
  const [report, setReport] = useState<AIConnections | null>(null);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [checking, setChecking] = useState(false);
  const [opened, setOpened] = useState(new Set<string>());
  const [changing, setChanging] = useState<Connection | null>(null);
  const [editingApi, setEditingApi] = useState<ApiGroup | "new" | null>(null);
  const [signIn, dispatch] = useReducer(signInReducer, idleSignIn);
  const session = useRef<Session | null>(null);
  const mounted = useRef(true);
  const reload = useCallback(async () => {
    setChecking(true);
    setError("");
    try {
      const result = await api.tag(["settings", "ai", "connections", "--json"]);
      if (!mounted.current) return;
      if (result.code) setError(failureLine(result, "Couldn't check AI connections."));
      else setReport(parseConnections(result.stdout));
    } catch (e) { if (mounted.current) setError(String(e)); }
    finally { if (mounted.current) setChecking(false); }
  }, [api]);
  useEffect(() => {
    mounted.current = true;
    void reload();
    return () => { mounted.current = false; session.current?.stop(); };
  }, [reload]);
  useEffect(() => {
    if (!signIn.result) return;
    const agent = report?.connections.find((c) => c.backend === signIn.result!.backend)?.name ?? "AI";
    setToast(resultLine(signIn.result, agent));
    void reload();
    void tags.refresh();
  }, [signIn.result]);

  const start = async (backend: string, method?: string, resume = false) => {
    setChanging(null);
    setToast("");
    dispatch({ type: "start", backend });
    try {
      const next = await api.setup(signInArgs(backend, { method, resume, restart: true }),
        (line) => mounted.current && dispatch({ type: "line", line }),
        (code, stderr) => { if (mounted.current) dispatch({ type: "exit", code, stderr }); session.current = null; });
      if (mounted.current) session.current = next;
      else next.stop();
    } catch (e) { dispatch({ type: "exit", code: 1, stderr: String(e) }); }
  };
  const act = (action: RowAction) => {
    if (action.kind === "install" || action.kind === "update") {
      void api.open(action.url);
      setOpened((set) => new Set(set).add(action.backend));
    } else if (action.kind === "change_account") {
      setChanging(report?.connections.find((c) => c.backend === action.backend) ?? null);
    } else void start(action.backend, undefined, action.kind === "resume");
  };
  const busy = !!signIn.step;
  const apiChanged = (text: string) => { setToast(text); void reload(); void tags.refresh(); };
  const tagChoices = tags.rows.filter((row) => row.valid !== false && row.state !== "setup_incomplete")
    .map((row) => ({ id: row.id, name: title(row) }));
  return <>
    <CompactSky title="AI connections" sub="Shared by all your Tags" back={close} />
    <div className="body river">
      <div className="section">
        <div className="sec-head"><h3>Connections</h3><span className="spacer" />
          {checking ? <span className="meta"><Spinner small />Checking…</span>
            : <button className="p-btn soft sm" disabled={busy} onClick={() => void reload()}>Check connections</button>}
        </div>
        <div className="card">
          {!report && !error ? <div className="conn"><span className="cstat"><span className="spin" />Checking this Mac…</span></div>
            : report?.connections.map((connection) => <ConnectionRow key={connection.backend} connection={connection}
              busy={busy || checking} signIn={signIn.backend === connection.backend ? signIn : null}
              quiet={!!report.usable.length && !report.usable.includes(connection.backend)} opened={opened.has(connection.backend)}
              act={act} cancel={() => session.current?.send({ cancel: true })} check={() => void reload()} open={(url) => void api.open(url)} />)}
        </div>
        <p className="mcap">Connect once for all Tags. Choose each Tag's model and thinking level in its Details tab.</p>
        {report?.running && <p className="mcap">Running Tags pause while you sign in and start again afterwards.</p>}
      </div>
      {apiConnections && report && <div className="section">
        <div className="sec-head"><h3>Your own API</h3><span className="spacer" />
          {!!report.api_connections?.length && <button className="p-btn soft sm" disabled={busy} onClick={() => setEditingApi("new")}>
            <Icon name="plus" size={11} />Add</button>}
        </div>
        {report.api_connections?.length
          ? <>
            <ApiList groups={groupApis(report.api_connections)} busy={busy} open={setEditingApi} />
            <p className="mcap">Used instead of your plan sign-in.</p>
          </>
          : <button className="ghost-add" disabled={busy} onClick={() => setEditingApi("new")}>
            <span className="gi"><Icon name="plus" /></span>
            <span><b>Add your own API</b><span className="meta">Use an OpenAI, Anthropic, or Azure OpenAI key instead of your plan.</span></span>
          </button>}
      </div>}
      {toast && <div className="savebar" role="status"><span className="t"><Icon name="check" size={12} />{toast}</span></div>}
      {error && <ErrorLine>{error}</ErrorLine>}
      <RiverFoot />
    </div>
    {changing && !busy && <ChangeAccount connection={changing} running={report?.running ?? false}
      cancel={() => setChanging(null)} choose={(method) => void start(changing.backend, method)} />}
    {editingApi && <ApiDialog api={api} tags={tagChoices} initial={editingApi === "new" ? null : editingApi}
      running={tags.rows.some((r) => r.state === "running" && (editingApi === "new" || editingApi.tags.some((t) => t.id === r.id)))}
      close={() => setEditingApi(null)} saved={apiChanged} />}
  </>;
}
