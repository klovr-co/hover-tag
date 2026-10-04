// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// AI connections, shared by setup's AI step and Settings → AI & models. Tag
// checks and signs in; these only draw what it reports.
import { useState } from "react";
import {
  ACTION_LABEL, CLAUDE_SHARED_NOTE, CODEX_METHODS, isUrgent, primaryAction, resultLine, statusLine,
  type Connection, type ModelGroup, type SignInResult, type SignInState,
} from "../lib/ai";
import claude from "../assets/agents/claude.png";
import codexDark from "../assets/agents/codex-dark.png";
import codexLight from "../assets/agents/codex.png";
import { Primary, Secondary, Spinner } from "./ui";

const TONE = { good: "var(--green)", muted: "var(--secondary)", warn: "var(--orange)", bad: "var(--red)", busy: "var(--accent)" };

const NAME: Record<string, string> = { codex: "Codex", claude: "Claude" };

/** The agent's own app icon; it names the agent, so rows don't repeat it. */
export function AgentMark({ backend, size = 32 }: { backend: string; size?: number }) {
  const name = NAME[backend] ?? backend;
  const style = { width: size, height: size, borderRadius: size * 0.24, flex: "none" as const, display: "block" };
  return backend === "codex" ? (
    <picture title={name}>
      <source srcSet={codexDark} media="(prefers-color-scheme: dark)" />
      <img src={codexLight} alt={name} style={style} />
    </picture>
  ) : <img src={claude} alt={name} title={name} style={style} />;
}

export type RowAction =
  | { kind: "sign_in" | "reconnect" | "resume"; backend: string }
  | { kind: "change_account"; backend: string }
  | { kind: "install" | "update"; backend: string; url: string };

interface RowProps {
  connection: Connection;
  /** This backend's sign-in, while one runs or just ended. */
  signIn: SignInState | null;
  /** Install guides opened from here; their button becomes Check again. */
  opened: boolean;
  busy: boolean;
  /** Another agent is already connected, so Continue is the main button and this one stays quiet. */
  quiet?: boolean;
  act: (action: RowAction) => void;
  cancel: () => void;
  check: () => void;
  open: (url: string) => void;
}

/** One agent: what it is, whether it's connected and verified, and the one thing to do next. */
export function ConnectionRow({ connection: c, signIn, opened, busy, quiet, act, cancel, check, open }: RowProps) {
  const running = signIn?.backend === c.backend && !!signIn.step;
  const result = signIn?.backend === c.backend ? signIn.result : null;
  const line = running ? { text: signIn.text || "Waiting for your browser…", tone: "busy" as const } : statusLine(c);
  const action = primaryAction(c);
  const button = () => {
    if (running) return <Secondary title="Cancel" onClick={cancel} />;
    if (!action) return null;
    if ((action === "install" || action === "update") && opened) return <Secondary title="Check again" onClick={check} disabled={busy} />;
    const title = ACTION_LABEL[action] ?? action;
    const run = () => act(action === "install" || action === "update"
      ? { kind: action, backend: c.backend, url: c.install_url }
      : { kind: action as "sign_in", backend: c.backend });
    return (isUrgent(action) || action === "resume") && !quiet
      ? <Primary title={title} onClick={run} disabled={busy} />
      : <Secondary title={title} onClick={run} disabled={busy} icon={action === "install" || action === "update" ? "external" : undefined} />;
  };
  return (
    <div className="row gap-12" style={{ padding: "10px 14px", minHeight: 54 }} aria-label={c.name}>
      <AgentMark backend={c.backend} />
      <div className="stack gap-4" style={{ flex: 1, minWidth: 0 }}>
        <div className="row gap-6" style={{ color: TONE[line.tone] }}>
          {running ? <Spinner small /> : <span className="dot" style={{ background: TONE[line.tone] }} />}
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={line.text}>{line.text}</span>
        </div>
        {running && signIn.url && (
          <div><button className="link" onClick={() => open(signIn.url!)}>Open the sign-in page again</button></div>
        )}
        {!running && c.state !== "connected" && c.state !== "unsupported" && c.detail && (
          <div className="caption secondary">{c.detail}</div>
        )}
        {!running && c.state === "limited" && (
          <div><button className="link" onClick={() => open("https://chatgpt.com/settings/usage")}>Review usage</button></div>
        )}
        {result && result.status !== "connected" && <ResultLine result={result} name={c.name} />}
        {opened && !running && (action === "install" || action === "update") && (
          <div className="caption secondary">Finish installing {c.name}, then check again.</div>
        )}
      </div>
      <div style={{ flex: "none" }}>{button()}</div>
    </div>
  );
}

function ResultLine({ result, name }: { result: SignInResult; name: string }) {
  return (
    <div className="caption" role={result.status === "failed" ? "alert" : undefined}
      style={{ color: result.status === "failed" ? "var(--red)" : "var(--secondary)" }}>
      {resultLine(result, name)}{result.status === "failed" && result.retry ? " Try again when you're ready." : ""}
    </div>
  );
}

/** Codex can use the computer's sign-in or a ChatGPT account for this Tag only; Claude's is always shared. */
export function ChangeAccount({ connection, tagName, running, choose, cancel }: {
  connection: Connection; tagName: string; running: boolean;
  choose: (method?: string) => void; cancel: () => void;
}) {
  const [method, setMethod] = useState<string>(connection.method === "chatgpt" ? "codex" : "chatgpt");
  const codex = connection.backend === "codex";
  const stops = running && codex && (method === "chatgpt" || connection.method === "chatgpt");
  return (
    <div className="well stack gap-10" role="dialog" aria-label={`Change ${connection.name} account`}>
      <div className="headline">Change {connection.name} account</div>
      {codex ? (
        <div className="card" role="radiogroup" aria-label="Account">
          {CODEX_METHODS.map((option) => (
            <label key={option.method} className="list-item" style={{ cursor: "pointer", alignItems: "flex-start" }}>
              <input type="radio" name="codex-method" checked={method === option.method}
                onChange={() => setMethod(option.method)} style={{ accentColor: "var(--accent)", marginTop: 2 }} />
              <span className="stack gap-4">
                <span style={{ fontWeight: 500 }}>{option.title(tagName)}</span>
                <span className="caption secondary">{option.detail}</span>
              </span>
            </label>
          ))}
        </div>
      ) : (
        <div className="secondary">{CLAUDE_SHARED_NOTE}</div>
      )}
      {stops && <div className="caption secondary">{tagName} stops while you sign in and starts again afterwards.</div>}
      <div className="row gap-8">
        <div className="spacer" />
        <Secondary title="Cancel" onClick={cancel} />
        <Primary title="Continue in browser" icon="external" onClick={() => choose(codex ? method : undefined)} autoFocus />
      </div>
    </div>
  );
}

/** One picker for every connected account's models, grouped by agent. */
export function ModelPicker({ groups, value, unavailable, onChange, disabled }: {
  groups: ModelGroup[]; value: string | null; unavailable?: { value: string; label: string } | null;
  onChange: (value: string) => void; disabled?: boolean;
}) {
  const backend = groups.find((group) => group.models.some((model) => model.value === value))?.backend;
  return (
    <div className="row gap-10">
      {backend && <AgentMark backend={backend} />}
      <select className="field" value={value ?? ""} disabled={disabled} aria-label="Default model"
      onChange={(e) => onChange(e.target.value)} style={{ appearance: "auto" }}>
      {!value && <option value="" disabled>Choose a model</option>}
      {unavailable && (
        <optgroup label="Not available">
          <option value={unavailable.value} disabled>{unavailable.label} · not available</option>
        </optgroup>
      )}
      {groups.map((group) => (
        <optgroup key={group.backend} label={group.name}>
          {group.models.map((model) => <option key={model.value} value={model.value}>{model.label}</option>)}
        </optgroup>
      ))}
      </select>
    </div>
  );
}

export function LoadingLine({ text }: { text: string }) {
  return <div className="row gap-8 secondary callout" style={{ padding: "4px 2px" }}><Spinner small />{text}</div>;
}

