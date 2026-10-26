// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// AI connections, shared by setup's AI step and Settings → AI & models. Tag
// checks and signs in; these only draw what it reports.
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  ACTION_LABEL, CLAUDE_SHARED_NOTE, CODEX_METHODS, isUrgent, primaryAction, statusLine,
  findModel, type AIModels, type AIStatus, type Connection, type ModelEntry, type ModelGroup, type PermissionRule, type SignInState,
} from "../lib/ai";
import { useNight } from "../lib/appearance";
import { reducedMotion, useFresh, useListMotion, usePresence } from "../lib/motion";
import { effortLabel } from "../lib/home";
import { choiceText, type ModelChoice, type SaveState } from "../lib/model";
import { optionsFor, savedValue, type PermissionsChoice, type RuleBackend, type Setting } from "../lib/permissions";
import { RulesEditor } from "./Rules";
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

/** A ⋯ button that opens a short list of less common actions. */
function MoreMenu({ label, items, disabled }: { label: string; items: { title: string; onClick: () => void }[]; disabled?: boolean }) {
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
  return (
    <div className="more-wrap" ref={ref}>
      <button className="p-btn quiet sm more-btn" aria-haspopup="menu" aria-expanded={open} aria-label={label}
        disabled={disabled} onClick={() => setOpen(!open)}>
        <Icon name="more" size={18} />
      </button>
      {open && (
        <div className="menu" role="menu" aria-label={label}>
          {items.map((item) => (
            <button key={item.title} role="menuitem" onClick={() => { setOpen(false); item.onClick(); }}>{item.title}</button>
          ))}
        </div>
      )}
    </div>
  );
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
  // Reconnect and Resume renew the saved account; this is the way to a different one.
  const another = c.actions.includes("change_account")
    ? <MoreMenu label={`More options for ${c.name}`} disabled={busy && !running}
        items={[{ title: "Change account", onClick: () => run("change_account") }]} />
    : null;
  if (running) {
    tone = "busy";
    line = <><span className="spin" />{signIn.text || "Waiting for your browser…"}</>;
    acts = button("quiet", "Cancel", cancel);
  } else if (failed) {
    tone = "bad";
    line = <><Icon name="warn" /><span>Sign-in didn't finish. {result?.error ?? ""}</span></>;
    acts = <>{button("ink", "Try again", () => run(c.state === "expired" ? "reconnect" : "sign_in"))}{another}</>;
  } else switch (c.state) {
    case "connected":
      tone = "ok";
      line = <><i className="dot" /><b>Connected</b>{c.account && <span className="acct">· {c.account}</span>}</>;
      acts = another;
      break;
    case "expired":
      tone = "warn";
      line = <><Icon name="warn" />Sign-in expired</>;
      acts = <>{button("ink", "Reconnect", () => run("reconnect"))}{another}</>;
      break;
    case "limited":
      tone = "warn";
      line = <><Icon name="warn" />Usage limit reached</>;
      acts = <><button className="link" onClick={() => open("https://chatgpt.com/settings/usage")}>Review usage</button>
        {button("soft", "Resume", () => run("resume"))}{another}</>;
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
  // Start on the account Codex uses now, so changing it doesn't begin on the other option.
  const [method, setMethod] = useState<string>(connection.method === "codex" ? "codex" : "chatgpt");
  const codex = connection.backend === "codex";
  const stops = running;
  const back = codex && method === "codex" && connection.method === "chatgpt";
  // Cancelling fades the dialog out before it closes.
  const [leaving, setLeaving] = useState(false);
  const dismiss = useCallback(() => {
    if (reducedMotion()) { cancel(); return; }
    setLeaving(true);
    setTimeout(cancel, 160);
  }, [cancel]);
  useEffect(() => {
    const close = (e: KeyboardEvent) => { if (e.key === "Escape") dismiss(); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [dismiss]);
  return (
    <div className={leaving ? "veil2 leaving" : "veil2"} onClick={dismiss}>
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
          <button className="p-btn quiet" onClick={dismiss}>Cancel</button>
          <Primary title={back ? "Use Codex sign-in" : "Continue in browser"} onClick={() => choose(codex ? method : undefined)} autoFocus />
        </div>
      </div>
    </div>
  );
}

// ---- The model picker, thinking level and save bar, shared by Tag detail and AI & models ----

/** Every connected account's models, grouped by agent; picking a model picks its agent.
 *  With `onRefresh`, the open menu ends in a quiet "Refresh list" that reads the accounts' lists again. */
export function ModelMenu({ models, report, value, onChange, below, inline, disabled, onRefresh, refreshing, refreshError }: {
  models: AIModels | null; report: AIStatus | null; value: string | null; onChange: (value: string) => void;
  below?: boolean; inline?: boolean; disabled?: boolean;
  onRefresh?: () => void; refreshing?: boolean; refreshError?: string;
}) {
  const [open, setOpen] = useState(false);
  const menu = usePresence(open ? true : null, 120);
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
      {menu.shown && (
        <div className={`mmenu${inline ? " inline" : below ? " below" : ""}${menu.leaving ? " leaving" : ""}`} role="listbox" aria-label="Default model">
          {connections.filter((c) => c.allowed !== false && (models.groups.some((g) => g.backend === c.backend) || (value && !offered && backend === c.backend))).map((c) => {
            const group = models.groups.find((g) => g.backend === c.backend);
            return (
              <div key={c.backend}>
                <div className="mgroup" role="presentation"><AgentMark backend={c.backend} size={20} />{c.name}
                  {c.state === "limited" && <em>Usage limit reached</em>}</div>
                {group && <ModelOptions group={group} value={value} pick={(next) => { onChange(next); setOpen(false); }} />}
                {value && !offered && backend === c.backend && (
                  <button className="mopt" role="option" aria-selected="true" disabled>
                    <span className="mck"><Icon name="check" size={12} /></span><s>{label}</s><span className="mdef">No longer offered</span>
                  </button>
                )}
              </div>
            );
          })}
          {onRefresh && (
            <div className="mfoot">
              {refreshError && !refreshing && <span className="mferr" role="status" key={refreshError}>{refreshError}</span>}
              <button className="mrefresh" onClick={onRefresh} disabled={refreshing} aria-busy={refreshing || undefined}>
                {refreshing ? <><span className="spin" />Refreshing…</> : <><Icon name="refresh" size={12} />Refresh list</>}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** One agent's models in the open menu; models a refresh adds slide in, and ones it drops slide out. */
function ModelOptions({ group, value, pick }: { group: ModelGroup; value: string | null; pick: (value: string) => void }) {
  const listed = useListMotion(group.models, (m) => m.value);
  return <>{listed.map(({ item: m, state }) => (
    <button key={m.value} className={`mopt${state === "enter" ? " entering" : state === "leave" ? " leaving" : ""}`} role="option"
      aria-selected={m.value === value} disabled={state === "leave"} onClick={() => pick(m.value)}>
      <span className="mck">{m.value === value && <Icon name="check" size={12} />}</span>{m.label}
      {m.default && <span className="mdef">Default</span>}
    </button>
  ))}</>;
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

/** Choices that give the agent much more reach, as `setting=value`. */
const WARN = new Set(["codex-sandbox=danger-full-access", "claude-mode=bypassPermissions", "claude-chrome=1"]);

/** Tag's reason it won't run with the saved value, while that's still the one shown. */
const reasonFor = (choice: PermissionsChoice, setting: Setting) =>
  choice.value(setting) === savedValue(choice.report!, setting) ? choice.report!.errors?.[setting] : undefined;
/** A value Tag won't run with: one it reports as wrong, or one that isn't among the choices. */
const invalid = (choice: PermissionsChoice, setting: Setting) => !!reasonFor(choice, setting) ||
  !optionsFor(choice.report!, setting).some((o) => o.value === choice.value(setting));

/** One setting: its name, the agent's own choices, and what the picked one means. */
function PermissionSetting({ choice, setting, title, disabled }: {
  choice: PermissionsChoice; setting: Setting; title: string; disabled?: boolean;
}) {
  const value = choice.value(setting);
  const fresh = useFresh(value, 300);
  const options = optionsFor(choice.report!, setting);
  const picked = options.find((o) => o.value === value);
  const reason = reasonFor(choice, setting);
  const warn = WARN.has(`${setting}=${value}`);
  return (
    <div className="think pset">
      <span className="tlab">{title}</span>
      {options.length > 4 ? (
        // Too many to fit on one line, like Claude's modes: a menu instead.
        <select className="field psel" aria-label={title} value={value} disabled={disabled} onChange={(e) => choice.pick(setting, e.target.value)}>
          {!picked && <option value={value} disabled>{value}</option>}
          {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      ) : (
        <div className="segc" role="radiogroup" aria-label={title}>
          {options.map((o) => (
            <button key={o.value} role="radio" aria-checked={value === o.value} disabled={disabled} onClick={() => choice.pick(setting, o.value)}>
              {o.label}
            </button>
          ))}
        </div>
      )}
      {picked && !reason ? (
        <p key={picked.value} className={`${warn ? "mwarn" : "mcap"} pdet${fresh ? " fresh" : ""}`}>
          {warn && <Icon name="warn" />}<span>{picked.detail}</span>
        </p>
      ) : (
        // A saved value Tag doesn't know: the Tag won't run until one above is picked.
        <p className="mwarn"><Icon name="warn" /><span>“{value}” isn't a valid choice, so the Tag won't run. Pick one above.
          {reason && <span className="pwhy">{reason}</span>}</span></p>
      )}
    </div>
  );
}

/** Rules for this Tag, folded into one line until you open them. */
function TagRules({ choice, backend, disabled, openSettings }: {
  choice: PermissionsChoice; backend: RuleBackend; disabled?: boolean; openSettings?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const rules = choice.report![backend].rules;
  if (!rules) return null;
  const count = (list: PermissionRule[] | null | undefined, what: string) =>
    list === null || list === undefined ? `unreadable ${what}` : list.length ? `${list.length} ${what}` : "";
  const summary = [count(rules.tag, "for this Tag"), count(rules.folder, "in its folder"), count(rules.all_tags, "for all Tags")]
    .filter(Boolean).join(" · ") || "None yet";
  const busyRule = choice.ruleBusy?.startsWith(`${backend}:`) ? choice.ruleBusy.slice(backend.length + 1) : null;
  // Tag keeps Codex rules only in a Tag's own folder; in a folder of yours they'd change your own Codex too.
  const yours = backend === "codex" && choice.report!.own_folder === false;
  const blocked = yours && !!rules.all_tags?.some((r) => r.decision !== "allow");
  return (
    <div className="think pset">
      <button className="psum" type="button" aria-expanded={open} aria-label={`${backend === "codex" ? "Codex" : "Claude"} rules: ${summary}`}
        onClick={() => setOpen((v) => !v)}>
        <b>Rules</b><span className="m">{summary}</span><span className="link">{open ? "Done" : "Edit"}<Icon name="chevdown" size={12} /></span>
      </button>
      {open && (
        <div className="pmore">
          {yours ? (
            <p className={blocked ? "mwarn" : "mcap"}>{blocked && <Icon name="warn" />}<span>
              This Tag works in a folder of yours, so Tag keeps no Codex rules for it: they'd change your own Codex there too.
              {blocked && " Codex rules for all Tags block things, so this Tag won't run Codex until it has its own folder."}
            </span></p>
          ) : (
            <RulesEditor backend={backend} own={rules.tag ?? null} busy={!!disabled || choice.ruleBusy !== null} busyRule={busyRule}
              change={(action, rule) => choice.changeRule(backend, action, rule)}
              inherited={[
                { label: "in its folder", where: backend === "codex" ? ".codex/rules" : ".claude/settings.json", rules: rules.folder ?? null },
                { label: "for all Tags", rules: rules.all_tags, where: openSettings
                  ? <button className="link" type="button" onClick={openSettings}>Change them in Settings → AI connections</button>
                  : "change them in Settings → AI connections" },
                { label: "of your own", where: backend === "codex" ? "~/.codex/rules" : "~/.claude/settings.json", rules: rules.personal }]} />
          )}
        </div>
      )}
    </div>
  );
}

const AGENTS = { codex: "Codex", claude: "Claude" } as const;

/** The permissions of the agent the Tag's model runs on (`using`); every Slack request uses that
 *  model, so the other agent's settings wait until the Tag switches to one of its models.
 *  Without a saved model both show. Each setting uses the agent's own name; rules open on a click. */
export function PermissionsRow({ choice, using, disabled, openSettings }: {
  choice: PermissionsChoice; using?: string; disabled?: boolean; openSettings?: () => void;
}) {
  if (!choice.report) return null;
  const shown = (["codex", "claude"] as const).filter((backend) => !using || using === backend);
  return <>{shown.map((backend) => (
    <section key={backend} className="pagent" aria-label={`${AGENTS[backend]} permissions`}>
      <div className="phead"><AgentMark backend={backend} size={20} /><b>{AGENTS[backend]}</b></div>
      {backend === "codex" ? <>
        <PermissionSetting choice={choice} setting="codex-sandbox" title="Sandbox" disabled={disabled} />
        <PermissionSetting choice={choice} setting="codex-approvals" title="Approval policy" disabled={disabled} />
        {/* Never asks, so there is nothing to review, but Codex still won't start with a reviewer it doesn't know. */}
        {(choice.value("codex-approvals") !== "never" || invalid(choice, "codex-reviewer")) &&
          <PermissionSetting choice={choice} setting="codex-reviewer" title="Approvals reviewer" disabled={disabled} />}
        <TagRules choice={choice} backend="codex" disabled={disabled} openSettings={openSettings} />
      </> : <>
        <PermissionSetting choice={choice} setting="claude-mode" title="Mode" disabled={disabled} />
        <PermissionSetting choice={choice} setting="claude-chrome" title="Chrome" disabled={disabled} />
        <TagRules choice={choice} backend="claude" disabled={disabled} openSettings={openSettings} />
      </>}
    </section>
  ))}</>;
}

type Saving = { save: SaveState; dirty: boolean; discard: () => void; commit: (restart: boolean) => Promise<void> };

/** Changing a default asks before restarting a running Tag; `what` names the new choice. */
export function SaveBar({ choice, what, tagName, running, held }: {
  choice: Saving; what: string | null; tagName: string; running: boolean;
  /** Something else is saving, like a rule; Save waits for it. */
  held?: boolean;
}) {
  if (choice.save === "restarting") {
    return <div className="savebar busy" role="status"><span className="t"><span className="spin" />Restarting {tagName}…</span></div>;
  }
  if (choice.save === "saved") {
    return <div className="savebar ok" role="status"><span className="t"><Icon name="check" size={12} />Saved. {tagName} uses it next time it starts.</span></div>;
  }
  if (!choice.dirty || !what) return null;
  const busy = choice.save === "saving";
  return (
    <div className="savebar">
      <span className="t">{running ? `Restart ${tagName} to use ${what}.` : `${tagName} isn't running, so nothing restarts.`}</span>
      <button className="p-btn quiet sm" disabled={busy} onClick={choice.discard}>Cancel</button>
      <button className="p-btn ink sm" disabled={busy || held} onClick={() => void choice.commit(running)}>{running ? "Save and restart" : "Save"}</button>
    </div>
  );
}

/** The model's save bar: "Restart Maya's Tag to use Opus 5.5 · High thinking." */
export const modelSaveText = (choice: ModelChoice) => choice.entry ? choiceText(choice.entry.label, choice.level) : null;
