// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// AI connections, shared by setup's AI step and Settings → AI & models. Tag
// checks and signs in; these only draw what it reports.
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  ACTION_LABEL, CLAUDE_SHARED_NOTE, CODEX_METHODS, isUrgent, primaryAction, statusLine,
  findModel, type AIModels, type AIStatus, type Connection, type ModelEntry, type SignInState,
} from "../lib/ai";
import { useNight } from "../lib/appearance";
import { effortLabel } from "../lib/home";
import { choiceText, type ModelChoice } from "../lib/model";
import claude from "../assets/agents/claude.png";
import codexDark from "../assets/agents/codex-dark.png";
import codexLight from "../assets/agents/codex.png";
import { Icon, Primary } from "./ui";


const NAME: Record<string, string> = { codex: "Codex", claude: "Claude" };

/** The agent's own app icon; it names the agent, so rows don't repeat it. */
export function AgentMark({ backend, size = 32 }: { backend: string; size?: number }) {
  const name = NAME[backend] ?? backend;
  const night = useNight();
  const style = { width: size, height: size, borderRadius: size * 0.24, flex: "none" as const, display: "block" };
  return backend === "codex"
    ? <img src={night ? codexDark : codexLight} alt={name} title={name} style={style} />
    : <img src={claude} alt={name} title={name} style={style} />;
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
  /** Another agent is already connected, so this row's sign-in isn't the page's main action. */
  quiet?: boolean;
  tagName?: string;
  act: (action: RowAction) => void;
  cancel: () => void;
  check: () => void;
  open: (url: string) => void;
}

/** One agent: its own app icon, one status line, and the one thing to do next. */
export function ConnectionRow({ connection: c, signIn, opened, busy, quiet, act, cancel, check, open }: RowProps) {
  const running = signIn?.backend === c.backend && !!signIn.step;
  const result = signIn?.backend === c.backend ? signIn.result : null;
  const failed = !running && result?.status === "failed";
  const action = primaryAction(c);
  const run = (kind: string) => act(kind === "install" || kind === "update"
    ? { kind, backend: c.backend, url: c.install_url } as RowAction : { kind, backend: c.backend } as RowAction);
  const button = (kind: "ink" | "soft" | "quiet", title: string, onClick: () => void, icon?: string) => (
    <button className={`p-btn ${quiet && kind === "ink" ? "soft" : kind} sm`} disabled={busy && !running} onClick={onClick}>
      {icon && <Icon name={icon} />}{title}
    </button>
  );
  let tone = "";
  let line: ReactNode;
  let acts: ReactNode = null;
  if (running) {
    tone = "busy";
    line = <><span className="spin" />{signIn.text || "Waiting for your browser…"}</>;
    acts = button("quiet", "Cancel", cancel);
  } else if (failed) {
    tone = "bad";
    line = <><Icon name="warn" /><span>Sign-in didn't finish. {result?.error ?? ""}</span></>;
    acts = button("ink", "Try again", () => run(c.state === "expired" ? "reconnect" : "sign_in"));
  } else switch (c.state) {
    case "connected":
      tone = "ok";
      line = <><i className="dot" /><b>Connected</b>{c.account && <span className="acct">· {c.account}</span>}</>;
      if (c.actions.includes("change_account")) acts = button("soft", "Change account", () => run("change_account"));
      break;
    case "expired":
      tone = "warn";
      line = <><Icon name="warn" />Sign-in expired</>;
      acts = button("ink", "Reconnect", () => run("reconnect"));
      break;
    case "limited":
      tone = "warn";
      line = <><Icon name="warn" />Usage limit reached</>;
      acts = <><button className="link" onClick={() => open("https://chatgpt.com/settings/usage")}>Review usage</button>
        {button("soft", "Resume", () => run("resume"))}</>;
      break;
    case "not_installed":
      line = <><i className="dot" />Not installed</>;
      acts = opened ? button("soft", "Check again", check) : button("soft", "Install", () => run("install"), "external");
      break;
    case "unsupported":
      tone = "warn";
      line = <><Icon name="warn" />Update needed{c.detail ? ` · ${c.detail}` : ""}</>;
      acts = opened ? button("soft", "Check again", check) : button("soft", "How to update", () => run("update"), "external");
      break;
    default:
      line = <><i className="dot" />{statusLine(c).text}</>;
      if (action && action !== "change_account") acts = button(isUrgent(action) ? "ink" : "soft", ACTION_LABEL[action] ?? action, () => run(action));
  }
  const note = !running && result?.status === "cancelled" ? "Sign-in cancelled." : "";
  return (
    <div className="conn" data-backend={c.backend} aria-label={c.name}>
      <AgentMark backend={c.backend} size={34} />
      <div className="txt">
        <span className={`cstat ${tone}`}>{line}</span>
        {c.state === "expired" && c.detail && <span className="cver">{c.detail}</span>}
        {note && <span className="cver">{note}</span>}
        {running && signIn.url && <span className="cver"><button className="link" style={{ padding: 0 }} onClick={() => open(signIn.url!)}>Open the sign-in page again</button></span>}
      </div>
      <div className="cact">{acts}</div>
    </div>
  );
}

/** Both provider connections are shared by every Tag. */
export function ChangeAccount({ connection, running, choose, cancel }: {
  connection: Connection; running: boolean;
  choose: (method?: string) => void; cancel: () => void;
}) {
  const [method, setMethod] = useState<string>("chatgpt");
  const codex = connection.backend === "codex";
  const stops = running;
  const back = codex && method === "codex" && connection.method === "chatgpt";
  useEffect(() => {
    const close = (e: KeyboardEvent) => { if (e.key === "Escape") cancel(); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [cancel]);
  return (
    <div className="veil2" onClick={cancel}>
      <div className="dlg" role="dialog" aria-modal="true" aria-labelledby="dlg-t" onClick={(e) => e.stopPropagation()}>
        <h3 id="dlg-t">Change {connection.name} account</h3>
        {codex ? (
          <div className="card" role="radiogroup" aria-label="Codex account">
            {CODEX_METHODS.map((option) => (
              <button key={option.method} className={method === option.method ? "opt sel" : "opt"} role="radio"
                aria-checked={method === option.method} onClick={() => setMethod(option.method)}>
                <span className={method === option.method ? "radio on" : "radio"} style={{ marginLeft: 0 }} />
                <span className="txt"><span className="label">{option.title()}</span><span className="sub wrap">{option.detail}</span></span>
              </button>
            ))}
          </div>
        ) : <p>{CLAUDE_SHARED_NOTE}</p>}
        {stops && <div className="dlg-note"><Icon name="restart" /><span>Running Tags pause while you sign in and start again afterwards.</span></div>}
        <div className="foot">
          <span className="spacer" />
          <button className="p-btn quiet" onClick={cancel}>Cancel</button>
          <Primary title={back ? "Use Codex sign-in" : "Continue in browser"} onClick={() => choose(codex ? method : undefined)} autoFocus />
        </div>
      </div>
    </div>
  );
}

// ---- The model picker, thinking level and save bar, shared by Tag detail and AI & models ----

/** Every connected account's models, grouped by agent; picking a model picks its agent. */
export function ModelMenu({ models, report, value, onChange, below, inline, disabled }: {
  models: AIModels | null; report: AIStatus | null; value: string | null; onChange: (value: string) => void;
  below?: boolean; inline?: boolean; disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", close);
    return () => { window.removeEventListener("mousedown", close); window.removeEventListener("keydown", close); };
  }, [open]);
  if (!models) {
    return <div className="mpick loading" aria-busy="true"><span className="spin" />Loading models from your accounts…</div>;
  }
  const hit = value ? findModel(models.groups, value) : null;
  const saved = report?.default_model;
  const label = hit?.entry.label ?? (saved && saved.value === value ? saved.label : value ?? "");
  const backend = hit?.group.backend ?? (value ?? "").split(":")[0];
  const offered = !!hit;
  const connections = report?.connections ?? [];
  return (
    <div className="mpick-wrap" ref={ref}>
      <button className={value && !offered ? "mpick bad" : "mpick"} aria-haspopup="listbox" aria-expanded={open}
        aria-label="Default model" disabled={disabled} onClick={() => setOpen(!open)}>
        {value ? <><AgentMark backend={backend} size={20} /><span className="mv">{label}</span>
          {!offered && <span className="mna">Not available</span>}</> : <span className="mb">Choose a model</span>}
        <span className="caret"><Icon name="updown" /></span>
      </button>
      {open && (
        <div className={inline ? "mmenu inline" : below ? "mmenu below" : "mmenu"} role="listbox" aria-label="Default model">
          {connections.filter((c) => c.allowed !== false && (models.groups.some((g) => g.backend === c.backend) || (value && !offered && backend === c.backend))).map((c) => {
            const group = models.groups.find((g) => g.backend === c.backend);
            return (
              <div key={c.backend}>
                <div className="mgroup" role="presentation"><AgentMark backend={c.backend} size={20} />{c.name}
                  {c.state === "limited" && <em>Usage limit reached</em>}</div>
                {group?.models.map((m) => (
                    <button key={m.value} className="mopt" role="option" aria-selected={m.value === value}
                      onClick={() => { onChange(m.value); setOpen(false); }}>
                      <span className="mck">{m.value === value && <Icon name="check" size={12} />}</span>{m.label}
                      {m.default && <span className="mdef">Default</span>}
                    </button>
                  ))}
                {value && !offered && backend === c.backend && (
                  <button className="mopt" role="option" aria-selected="true" disabled>
                    <span className="mck"><Icon name="check" size={12} /></span><s>{label}</s><span className="mdef">No longer offered</span>
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** "{Model} isn't available from your connected accounts." when the saved model is gone. */
export function ModelWarning({ models, value, label, tail }: { models: AIModels | null; value: string | null; label: string; tail: string }) {
  if (!models || !value || findModel(models.groups, value)) return null;
  return <div className="mwarn" role="status"><Icon name="warn" /><span>{label} isn't available from your connected accounts. {tail}</span></div>;
}

/** The model's own thinking levels; the dot marks its default. */
export function ThinkingRow({ entry, value, onChange, disabled }: {
  entry: ModelEntry | null; value: string | null; onChange: (effort: string) => void; disabled?: boolean;
}) {
  if (!entry) return null;
  const levels = entry.efforts ?? [];
  if (!levels.length) {
    return <div className="think"><span className="tlab">Thinking</span><span className="meta">{entry.label} doesn't have thinking levels.</span></div>;
  }
  return (
    <div className="think">
      <span className="tlab">Thinking</span>
      <div className="segc" role="radiogroup" aria-label="Thinking level">
        {levels.map((level) => (
          <button key={level} role="radio" aria-checked={value === level} disabled={disabled} onClick={() => onChange(level)}
            title={level === entry.default_effort ? `${entry.label}'s default` : undefined}>
            {effortLabel(level)}{level === entry.default_effort && <i className="tdef" aria-label="default" />}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Changing a default asks before restarting a running Tag. */
export function SaveBar({ choice, tagName, running }: { choice: ModelChoice; tagName: string; running: boolean }) {
  if (choice.save === "restarting") {
    return <div className="savebar busy" role="status"><span className="t"><span className="spin" />Restarting {tagName}…</span></div>;
  }
  if (choice.save === "saved") {
    return <div className="savebar ok" role="status"><span className="t"><Icon name="check" size={12} />Saved. {tagName} uses it next time it starts.</span></div>;
  }
  if (!choice.dirty || !choice.entry) return null;
  const busy = choice.save === "saving";
  return (
    <div className="savebar">
      <span className="t">{running ? `Restart ${tagName} to use ${choiceText(choice.entry.label, choice.level)}.` : `${tagName} isn't running, so nothing restarts.`}</span>
      <button className="p-btn quiet sm" disabled={busy} onClick={choice.discard}>Cancel</button>
      <button className="p-btn ink sm" disabled={busy} onClick={() => void choice.commit(running)}>{running ? "Save and restart" : "Save"}</button>
    </div>
  );
}
