// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Settings → AI & models: each Tag's agent connections and default model.
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { Bridge, Session } from "../lib/bridge";
import {
  aiArgs, choiceLabel, findModel, idleSignIn, needsRestart, parseModels, parseStatus, resultLine, selectedModel,
  signInArgs, signInReducer, type AIModels, type AIStatus, type Connection,
} from "../lib/ai";
import { status as tagStatus, title, type TagRow } from "../lib/protocol";
import { failureLine, type Tags } from "../lib/tags";
import { ChangeAccount, ConnectionRow, LoadingLine, ModelPicker, type RowAction } from "./AI";
import { Back, ErrorLine, Heading, Icon, Primary, Secondary } from "./ui";

/** Tags that finished setup; the others have no agent settings yet. */
export const configuredTags = (rows: TagRow[]) => rows.filter((row) => row.valid && tagStatus(row) !== "setup");

export function AISettings({ api, tags, initial, close }: { api: Bridge; tags: Tags; initial?: string; close: () => void }) {
  const choices = configuredTags(tags.rows);
  const [tagId, setTagId] = useState(initial && choices.some((r) => r.id === initial) ? initial : choices[0]?.id ?? "");
  const row = choices.find((r) => r.id === tagId) ?? null;
  const tagName = row ? title(row) : "Tag";
  const [report, setReport] = useState<AIStatus | null>(null);
  const [models, setModels] = useState<AIModels | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkedAt, setCheckedAt] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [picked, setPicked] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [restartNeeded, setRestartNeeded] = useState(false);
  const [opened, setOpened] = useState(new Set<string>());
  const [changing, setChanging] = useState<Connection | null>(null);
  const [signIn, dispatch] = useReducer(signInReducer, idleSignIn);
  const session = useRef<Session | null>(null);
  const current = useRef(tagId);
  current.current = tagId;

  const loadModels = useCallback(async (id: string) => {
    setModels(null);
    const result = await api.tag(aiArgs(id, "models", "--json"));
    if (current.current !== id) return;
    if (result.code === 0) setModels(parseModels(result.stdout));
    else setError(failureLine(result, "Couldn't load models from your accounts."));
  }, [api]);

  const check = useCallback(async (id = current.current) => {
    if (!id) return;
    setChecking(true);
    setError("");
    try {
      const result = await api.tag(aiArgs(id, "--json"));
      if (current.current !== id) return;
      if (result.code !== 0) { setError(failureLine(result, "Couldn't check this Tag's AI connections.")); return; }
      const next = parseStatus(result.stdout);
      setReport(next);
      setCheckedAt(Date.now());
      if (next.usable.length) void loadModels(id);
      else setModels(null);
    } catch (e) {
      setError(String(e));
    } finally {
      if (current.current === id) setChecking(false);
    }
  }, [api, loadModels]);

  useEffect(() => {
    setReport(null); setModels(null); setPicked(null); setNotice(""); setRestartNeeded(false);
    setChanging(null); setOpened(new Set()); dispatch({ type: "reset" });
    void check(tagId);
  }, [tagId, check]);

  // A sign-in that's still running when the screen closes is cancelled, not left behind.
  useEffect(() => () => session.current?.stop(), []);

  const startSignIn = async (backend: string, options: { method?: string; resume?: boolean } = {}) => {
    if (!report) return;
    const connection = report.connections.find((c) => c.backend === backend);
    const restart = report.running && !!connection && (options.resume || needsRestart(connection, options.method));
    setNotice(""); setChanging(null);
    dispatch({ type: "start", backend });
    session.current = await api.setup(signInArgs(tagId, backend, { ...options, restart }),
      (line) => dispatch({ type: "line", line }),
      (code, stderr) => { dispatch({ type: "exit", code, stderr }); session.current = null; });
  };

  // When a sign-in ends, check again and say what changed.
  useEffect(() => {
    const result = signIn.result;
    if (!result) return;
    const name = report?.connections.find((c) => c.backend === result.backend)?.name ?? "The agent";
    if (result.status === "connected") {
      setNotice(resultLine(result, name));
      if (result.restart_required) setRestartNeeded(true);
      void check();
    }
    if (result.status === "cancelled") dispatch({ type: "reset" });
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

  const save = async (restart: boolean) => {
    if (!picked) return;
    setSaving(true);
    setError("");
    const result = await api.tag(aiArgs(tagId, "model", picked, ...(restart ? ["--restart"] : []), "--json"));
    setSaving(false);
    if (result.code !== 0) { setError(failureLine(result, "Couldn't save the default model.")); return; }
    const saved = JSON.parse(result.stdout.slice(result.stdout.indexOf("{"))) as { default_model: AIStatus["default_model"]; restarted: boolean };
    setPicked(null);
    setReport((r) => r && { ...r, default_model: saved.default_model });
    setModels((m) => m && { ...m, default: { ...saved.default_model, available: true } });
    setNotice(saved.restarted ? `${tagName} restarted with ${saved.default_model.label}.`
      : report?.running ? "" : `Saved. ${tagName} uses it next time it starts.`);
    if (!saved.restarted && report?.running) setRestartNeeded(true);
    void tags.refresh();
  };

  const restartNow = async () => {
    setSaving(true);
    const result = await api.tag([tagId, "restart"]);
    setSaving(false);
    if (result.code !== 0) setError(failureLine(result, `Couldn't restart ${tagName}.`));
    else { setRestartNeeded(false); setNotice(`${tagName} restarted.`); }
    void tags.refresh();
  };

  const signingIn = !!signIn.step;
  const shown = models ? selectedModel(models) : null;
  const value = picked ?? shown;
  const dirty = !!picked && picked !== shown;
  const pickedModel = models && picked ? findModel(models.groups, picked) : null;
  const gone = models && !models.default.available && models.default.chosen
    ? { value: models.default.value, label: choiceLabel(models.default) } : null;
  const ago = checkedAt && Date.now() - checkedAt < 60_000 ? "Checked just now" : checkedAt ? "Checked earlier" : "";

  if (!choices.length) {
    return (
      <div className="stack gap-20">
        <div className="row"><Back onClick={close} /></div>
        <Heading title="No Tags yet" body="Each Tag has its own default model. Add a Tag first." />
      </div>
    );
  }
  return (
    <div className="stack gap-20">
      <div className="row gap-10"><Back onClick={close} /><div className="spacer" /></div>
      <Heading title="AI & models" />
      <div className="stack gap-6">
        <select className="field" aria-label="Tag" value={tagId} disabled={signingIn || saving}
          onChange={(e) => setTagId(e.target.value)} style={{ appearance: "auto" }}>
          {choices.map((r) => (
            <option key={r.id} value={r.id}>{title(r)}{r.workspace_name ? ` · ${r.workspace_name}` : ""}</option>
          ))}
        </select>
      </div>

      <div className="stack gap-8">
        <div className="row gap-8" style={{ padding: "0 4px" }}>
          <span className="headline">Connections</span>
          <div className="spacer" />
          {ago && !checking && <span className="caption secondary">{ago}</span>}
          <Secondary title={checking ? "Checking…" : "Check connections"} icon="refresh"
            disabled={checking || signingIn} onClick={() => void check()} />
        </div>
        <div className="card">
          {!report ? <div style={{ padding: 14 }}><LoadingLine text="Checking this Mac…" /></div>
            : report.connections.filter((c) => c.allowed !== false).map((connection, index) => (
              <div key={connection.backend}>
                {index > 0 && <div className="divider" style={{ marginLeft: 58 }} />}
                <ConnectionRow connection={connection} busy={signingIn || checking}
                  signIn={signIn.backend === connection.backend ? signIn : null}
                  opened={opened.has(connection.backend)} act={act}
                  cancel={() => session.current?.send({ cancel: true })}
                  check={() => void check()} open={(url) => void api.open(url)} />
              </div>
            ))}
        </div>
        {changing && !signingIn && (
          <ChangeAccount connection={changing} tagName={tagName} running={!!report?.running}
            cancel={() => setChanging(null)} choose={(method) => void startSignIn(changing.backend, { method })} />
        )}
        {report && !report.usable.length && (
          <div className="caption secondary" style={{ padding: "0 4px" }}>
            {tagName} can't answer in Slack until Codex or Claude is connected.
          </div>
        )}
      </div>

      <div className="stack gap-8">
        <div className="headline" style={{ padding: "0 4px" }}>Default model</div>
        {report && !report.usable.length ? (
          <div className="well secondary">Connect Codex or Claude to choose a model.</div>
        ) : !models ? (
          <div className="well"><LoadingLine text="Loading models from your accounts…" /></div>
        ) : (
          <div className="stack gap-8">
            {gone && !picked && (
              <div className="row gap-6 callout" style={{ color: "var(--orange)" }} role="alert">
                <Icon name="warning" />{models.default.label} isn't available from your connected accounts. Pick another so new tasks don't fail.
              </div>
            )}
            <ModelPicker groups={models.groups} value={value} unavailable={gone} disabled={saving || signingIn}
              onChange={(next) => setPicked(next)} />
            <span className="caption secondary" style={{ padding: "0 4px" }}>
              Also picks the agent. People's own choices in Slack are kept.
            </span>
          </div>
        )}
        {(dirty || (gone && picked)) && pickedModel && (
          <div className="well row gap-10">
            <span className="callout" style={{ flex: 1 }}>
              {report?.running ? `Restart ${tagName} to use ${pickedModel.entry.label}.` : `${tagName} will use ${pickedModel.entry.label} by default.`}
            </span>
            <Secondary title="Cancel" onClick={() => setPicked(null)} disabled={saving} />
            {report?.running
              ? <Primary title={saving ? "Restarting…" : "Save and restart"} onClick={() => void save(true)} disabled={saving} />
              : <Primary title={saving ? "Saving…" : "Save"} onClick={() => void save(false)} disabled={saving} />}
          </div>
        )}
        {restartNeeded && !dirty && (
          <div className="well row gap-10">
            <span className="callout" style={{ flex: 1 }}>Restart {tagName} to use this change.</span>
            <Secondary title="Later" onClick={() => setRestartNeeded(false)} disabled={saving} />
            <Primary title={saving ? "Restarting…" : "Restart now"} onClick={() => void restartNow()} disabled={saving} />
          </div>
        )}
      </div>
      {notice && <div className="row gap-6 callout" style={{ color: "var(--green)" }} role="status"><Icon name="check" />{notice}</div>}
      {error && <ErrorLine>{error}</ErrorLine>}
    </div>
  );
}

/** The Settings row that opens AI & models, with a quick summary for the first Tag. */
export function AISummaryRow({ api, tags, open }: { api: Bridge; tags: Tags; open: () => void }) {
  const first = configuredTags(tags.rows)[0];
  const [summary, setSummary] = useState("");
  useEffect(() => {
    if (!first) { setSummary("Set up a Tag to choose its model."); return; }
    let live = true;
    void api.tag(aiArgs(first.id, "--json")).then((r) => {
      if (!live || r.code !== 0) return;
      const report = parseStatus(r.stdout);
      const many = configuredTags(tags.rows).length > 1 ? `${title(first)}: ` : "";
      setSummary(`${many}${report.usable.length} of ${report.connections.length} connected · Default: ${choiceLabel(report.default_model)}`);
    }).catch(() => {});
    return () => { live = false; };
  }, [api, first?.id, tags.rows, first]);
  return (
    <button className="card list-item" onClick={open} style={{ gap: 12 }}>
      <span className="stack gap-4" style={{ flex: 1 }}>
        <span style={{ fontWeight: 500 }}>AI & models</span>
        <span className="caption secondary">{summary || "Connections and each Tag's default model"}</span>
      </span>
      <span className="tertiary"><Icon name="right" size={11} /></span>
    </button>
  );
}
