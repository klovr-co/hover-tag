// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Settings → AI connections → Your own API: Tags that bill through an API key
// instead of the shared sign-in. Tag checks, saves and restarts; this only
// collects the form and draws what `tag … settings ai api` reports.
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import type { Bridge } from "../lib/bridge";
import {
  API_AGENTS, apiCheckArgs, apiClearArgs, apiFormProblem, apiKind, apiLabel, apiSetArgs, hostOf, modelList, parseApiLine,
  type ApiCheck, type ApiForm, type ApiGroup, type ApiResult,
} from "../lib/ai";
import { reducedMotion, useListMotion } from "../lib/motion";
import { parseJSON } from "../lib/protocol";
import { failureLine } from "../lib/tags";
import { AgentMark } from "./AI";
import { ErrorLine, Icon, Primary, Spinner } from "./ui";

export interface ApiTag { id: string; name: string }

/** Runs one `api set|clear` and resolves with its result. The key, if any, goes over stdin only. */
export async function runApi(api: Bridge, args: string[], key: string | null, onText: (text: string) => void): Promise<ApiResult> {
  return new Promise((resolve) => {
    let result: ApiResult | null = null;
    api.setup(args, (line) => {
      const event = parseApiLine(line);
      if (!event) return;
      if ("type" in event) result = event;
      else onText(event.text);
    }, (code, stderr) => resolve(result ?? { type: "api", action: args[4], backend: "", status: "failed",
      error: stderr.trim().split("\n").pop()?.replace(/^Error: /, "") || `Tag stopped unexpectedly (${code}).` }))
      .then((session) => { if (key !== null) session.send({ api_key: key }); })
      .catch((e) => resolve({ type: "api", action: args[4], backend: "", status: "failed", error: String(e) }));
  });
}

const groupKey = (group: ApiGroup) => `${group.backend}:${group.base_url}:${group.tags.map((t) => t.id).join(",")}`;


/** APIs your Tags use in place of a sign-in. A row opens it, to check, change, or switch back. */
export function ApiList({ groups, open, busy }: {
  groups: ApiGroup[]; open: (group: ApiGroup) => void; busy: boolean;
}) {
  const listed = useListMotion(groups, groupKey);
  if (!listed.length) return null;
  return (
    <div className="card api-list">
      {listed.map(({ item, state }) => (
        <button key={groupKey(item)} className={`opt api-row${state === "enter" ? " entering" : ""}${state === "leave" ? " api-leave" : ""}`}
          data-backend={item.backend} aria-label={apiLabel(item)} disabled={busy} onClick={() => open(item)}>
          <AgentMark backend={item.backend} size={34} />
          <span className="txt">
            <span className={`cstat ${item.problem ? "warn" : "ok"}`}>
              {item.problem ? <Icon name="warn" /> : <i className="dot" />}<b>{apiLabel(item)}</b>
            </span>
            <span className={item.problem ? "sub" : "sub models"}>{item.problem || item.models.join(" · ")}</span>
          </span>
          <span className="chev"><Icon name="right" /></span>
        </button>
      ))}
    </div>
  );
}

const Field = ({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) =>
  <label className="api-field"><span>{label}{hint && <em> · {hint}</em>}</span>{children}</label>;

/** Add your own API, or open one. It applies to every Tag; a saved key is never shown or read back. */
export function ApiDialog({ api, tags, initial, running, close, saved }: {
  api: Bridge; tags: ApiTag[]; initial: ApiGroup | null; running: boolean;
  close: () => void; saved: (text: string) => void;
}) {
  const [form, setForm] = useState<ApiForm>(() => initial
    ? { backend: initial.backend as ApiForm["backend"], baseUrl: initial.base_url, models: initial.models.join(", "), apiVersion: initial.api_version }
    : { backend: "codex", baseUrl: "", models: "", apiVersion: "" });
  const [key, setKey] = useState("");
  const keySaved = !!initial?.key_set;
  const [replacing, setReplacing] = useState(!keySaved);
  /** Each saving Tag's latest step; every Tag saves at once. */
  const [progress, setProgress] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [check, setCheck] = useState<ApiCheck | "checking" | null>(null);
  const [leaving, setLeaving] = useState(false);
  const checked = useRef<HTMLDivElement>(null);
  useEffect(() => { if (check && check !== "checking") checked.current?.scrollIntoView?.({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" }); }, [check]);
  // Every Tag uses it, like the shared sign-ins; an open API changes the Tags already using it.
  const [targets, setTargets] = useState<ApiTag[]>(() => initial ? initial.tags : tags);
  const sending = Object.keys(progress).length > 0;
  const agent = API_AGENTS[form.backend];
  const azure = apiKind(form) === "azure";
  const models = modelList(form.models);
  const problem = apiFormProblem(form, key, keySaved, targets.length);
  const dismiss = useCallback(() => {
    if (sending) return;
    if (reducedMotion()) { close(); return; }
    setLeaving(true);
    setTimeout(close, 160);
  }, [close, sending]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") dismiss(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dismiss]);
  // A changed form goes to every Tag again, so no Tag keeps older values after a partial failure.
  const allTargets = () => setTargets(initial ? initial.tags : tags);
  const set = (patch: Partial<ApiForm>) => { setForm((f) => ({ ...f, ...patch })); setError(""); setCheck(null); allTargets(); };
  const fail = (text: string) => { setError(text); setAttempt((n) => n + 1); };
  const step = (tag: ApiTag, text: string) => setProgress((all) => ({ ...all, [tag.id]: `${tag.name}: ${text}` }));

  /** Runs one command for each Tag at once: separate Tags have separate settings and may restart together. */
  const forEach = async (args: (tag: ApiTag) => string[], key: string | null, first: string) => {
    targets.forEach((tag) => step(tag, first));
    const results = await Promise.all(targets.map(async (tag) =>
      ({ tag, result: await runApi(api, args(tag), key, (text) => step(tag, text)) })));
    setProgress({});
    const failed = results.filter((r) => r.result.status !== "saved");
    if (failed.length) {
      // Only the Tags that failed are tried again.
      setTargets(failed.map((r) => r.tag));
      fail(failed.map((r) => (results.length > 1 ? `${r.tag.name}: ` : "") + (r.result.error ?? "Couldn't save.")).join(" "));
    }
    return { done: results.length - failed.length, failed: failed.length, results };
  };
  const save = async () => {
    if (problem) { fail(problem); return; }
    setError("");
    const { done, failed, results } = await forEach((tag) => apiSetArgs(tag.id, form), key.trim(), "Checking…");
    const label = apiLabel({ backend: form.backend, kind: apiKind(form), host: hostOf(form.baseUrl || agent.url) });
    const elsewhere = results.filter((r) => r.result.status === "saved" && r.result.in_use === false).length;
    if (done) {
      saved(`${label} saved.` + (elsewhere ? ` To use it, choose a ${agent.name} model in a Tag's Details.` : ""));
    }
    if (failed) return;
    setKey("");
    close();
  };
  const runCheck = async () => {
    if (!initial) return;
    setError("");
    setCheck("checking");
    try {
      const result = await api.tag(apiCheckArgs(initial.tags[0].id, initial.backend));
      const report = result.stdout.trim() ? parseJSON<ApiCheck & { error?: string }>(result.stdout) : null;
      if (report?.checks) setCheck(report);
      else { setCheck(null); fail(report?.error || failureLine(result, "Couldn't check.")); }
    } catch (e) { setCheck(null); fail(String(e)); }
  };
  const switchBack = async () => {
    if (!initial) return;
    setError("");
    const { done, failed } = await forEach((tag) => apiClearArgs(tag.id, initial.backend), null, "Switching back…");
    if (done) saved(`Back on your ${agent.name} sign-in.`);
    if (!failed) close();
  };

  return (
    <div className={leaving ? "veil2 leaving" : "veil2"} onClick={dismiss}>
      <div className="dlg api-dlg" role="dialog" aria-modal="true" aria-labelledby="api-t" onClick={(e) => e.stopPropagation()}>
        <div className="api-head">
          {initial && <AgentMark backend={initial.backend} size={30} />}
          <div className="txt">
            <h3 id="api-t">{initial ? apiLabel(initial) : "Add your own API"}</h3>
            <p>{initial ? `Used instead of your ${agent.name} sign-in` : "Use your own API key instead of your plan."}</p>
          </div>
        </div>
        <div className="api-body">
          {!initial && <div className="api-field"><span>Agent</span>
            <div className="segc api-kinds" role="radiogroup" aria-label="Agent">
              {(["codex", "claude"] as const).map((backend) => (
                <button key={backend} role="radio" aria-checked={form.backend === backend} aria-label={API_AGENTS[backend].name}
                  disabled={sending} onClick={() => set({ backend, apiVersion: "" })}>
                  <AgentMark backend={backend} size={16} />{API_AGENTS[backend].name}
                </button>
              ))}
            </div>
            <span key={form.backend} className="api-hint">{agent.name} works with {agent.speaks}.</span>
          </div>}
          <Field label="Base URL" hint="optional">
            <input className="field mono" value={form.baseUrl} placeholder={agent.url} spellCheck={false} autoComplete="off"
              disabled={sending} onChange={(e) => set({ baseUrl: e.target.value })} />
          </Field>
          {azure && <div className="api-azure"><span className="api-badge">Azure OpenAI</span>
            <span>Models are your deployment names.</span></div>}
          {azure && <Field label="API version" hint="empty for the v1 API">
            <input className="field mono" value={form.apiVersion} placeholder="2025-04-01-preview" spellCheck={false} autoComplete="off"
              disabled={sending} onChange={(e) => set({ apiVersion: e.target.value })} />
          </Field>}
          <Field label={azure ? "Deployment names" : "Models"} hint="comma-separated">
            <input className="field mono" value={form.models} placeholder={agent.model}
              spellCheck={false} autoComplete="off" disabled={sending} onChange={(e) => set({ models: e.target.value })} />
          </Field>
          {models.length > 0 && <div className="api-models" aria-label="Models">{models.map((m, i) =>
            <span key={m} className={i ? "api-chip" : "api-chip first"} title={i ? undefined : "Used when a Tag's model isn't listed"}>
              {m}{!i && <em>default</em>}</span>)}</div>}
          {replacing
            ? <Field label="API key" hint={keySaved ? "replaces the saved key" : "stored on this computer, never shown again"}>
              <input className="field mono" type="password" value={key} placeholder="Paste key" autoComplete="new-password"
                spellCheck={false} disabled={sending} autoFocus={keySaved} onChange={(e) => { setKey(e.target.value); setError(""); allTargets(); }} />
            </Field>
            : <div className="api-field"><span>API key</span>
              <div className="api-saved"><Icon name="lock" size={13} /><span>Saved on this computer</span>
                <button className="link" disabled={sending} onClick={() => setReplacing(true)}>Replace</button></div>
            </div>}
          {initial && <div className="api-checkrow">
            <button className="p-btn soft sm" disabled={sending || check === "checking"} onClick={() => void runCheck()}>
              {check === "checking" ? <><Spinner small />Checking…</> : "Check connection"}</button>
            <span className="meta">No tokens spent.</span>
          </div>}
          {check && check !== "checking" && <div ref={checked} className={`api-check ${check.ok ? "ok" : "bad"}`} role="status">{check.checks.map((c) =>
            <span key={c.name}><Icon name={c.ok ? "check" : "warn"} size={12} />{c.text}</span>)}</div>}
        </div>
        {!sending && running && <div className="dlg-note"><Icon name="restart" />
          <span>Running Tags restart, and keep their previous connection if they can't start.</span></div>}
        {sending && <div className="api-progress" role="status">{Object.entries(progress).map(([id, text]) =>
          <div key={id} className="dlg-note"><Spinner small /><span key={text}>{text}</span></div>)}</div>}
        {/* A new key per attempt, so each failed save gives its one small shake. */}
        {error && <ErrorLine key={attempt}>{error}</ErrorLine>}
        <div className="foot">
          {initial && <button className="p-btn quiet api-back" disabled={sending} onClick={() => void switchBack()}>Switch back to my plan</button>}
          <span className="spacer" />
          <button className="p-btn quiet" disabled={sending} onClick={dismiss}>Cancel</button>
          <Primary title={sending ? "Saving…" : "Save"} onClick={() => void save()} disabled={sending} />
        </div>
      </div>
    </div>
  );
}
