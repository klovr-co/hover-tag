// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// One agent's permission rules: the ones this screen changes, and the ones that
// also apply but are changed elsewhere. A block anywhere wins over any allow.
import { useRef, useState, type ReactNode } from "react";
import type { PermissionRule } from "../lib/ai";
import { useListMotion } from "../lib/motion";
import type { RuleAction, RuleBackend } from "../lib/permissions";
import { Icon } from "./ui";

const KIND: Partial<Record<PermissionRule["kind"], string>> = { folder: "Folder", host: "Host" };
const DECISION: Record<PermissionRule["decision"], string> = { allow: "Allow", ask: "Ask", block: "Block" };
const HELP = {
  codex: { name: "Codex", examples: ["git push", "npm publish", "rm -rf"], label: "Command start", hint: "Commands that start with this." },
  claude: { name: "Claude", examples: ["Bash(git push:*)", "Bash(npm publish:*)", "WebFetch"], label: "Claude rule",
    hint: "A tool, or a tool and pattern, like WebFetch(domain:example.com)." },
} as const;

/** Rules that also apply, shown but changed elsewhere; null when their file couldn't be read. */
type InheritedRules = { label: string; where: ReactNode; rules: PermissionRule[] | null };

/** `going` dims the one row being changed; `busy` only stops the others changing meanwhile. */
function RuleRow({ rule, state, busy, going, onRemove }: {
  rule: PermissionRule; state?: "stay" | "enter" | "leave"; busy?: boolean; going?: boolean; onRemove?: () => void;
}) {
  return (
    <li className={state === "leave" ? "leaving" : state === "enter" ? "entering" : going ? "going" : undefined}>
      <span className={`pdec ${rule.decision}`}>{DECISION[rule.decision]}</span>
      {KIND[rule.kind] && <span className="pkind">{KIND[rule.kind]}</span>}
      <code>{rule.value}</code>
      {onRemove && (
        <button className="link" data-rule={rule.kind + rule.value} disabled={busy || state === "leave"} aria-label={`Remove ${rule.value}`}
          onClick={onRemove}>Remove</button>
      )}
    </li>
  );
}

/** Add, list, and remove one agent's rules; `busyRule` is the rule being changed, or "" while one is added.
 *  `own` is null when its file couldn't be read: nothing can be added until it can. */
export function RulesEditor({ backend, own, inherited, busy, busyRule, change }: {
  backend: RuleBackend; own: PermissionRule[] | null; inherited: InheritedRules[];
  busy: boolean; busyRule: string | null; change: (action: RuleAction, rule: string) => Promise<boolean>;
}) {
  // Added rules slide in, and removed ones slide out.
  const listed = useListMotion(own ?? [], (rule) => rule.kind + rule.value);
  const [text, setText] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const [decision, setDecision] = useState<"allow" | "block">("block");
  const [showInherited, setShowInherited] = useState(false);
  const help = HELP[backend];
  // An example that isn't already a rule here.
  const placeholder = help.examples.find((e) => !own?.some((rule) => rule.value === e)) ?? help.examples[0];
  const locked = busy || own === null;
  const add = async () => {
    if (!text.trim()) return;
    if (await change(decision, text.trim())) setText("");
    // Ready for the next one, or to fix this one: the field was disabled while it saved, which dropped focus.
    requestAnimationFrame(() => input.current?.focus());
  };
  // The Remove button was disabled while it saved, which dropped focus. Once the row is gone it moves to
  // the next row's Remove, or the one before, or the field when none are left; after a failure it stays.
  const remove = async (rule: PermissionRule) => {
    const rules = own ?? [];
    const at = rules.indexOf(rule);
    const ok = await change("remove", rule.value);
    const near = ok ? [rules[at + 1], rules[at - 1]] : [rule];
    requestAnimationFrame(() => {
      const buttons = [...list.current?.querySelectorAll<HTMLButtonElement>("li:not(.leaving) button[data-rule]") ?? []];
      const next = near.flatMap((r) => r ? buttons.filter((b) => b.dataset.rule === r.kind + r.value) : [])[0];
      (next ?? input.current)?.focus();
    });
  };
  const counted = inherited.filter((group) => group.rules === null || group.rules.length > 0);
  return (
    <div className="prules">
      {own === null ? (
        <p className="mwarn"><Icon name="warn" /><span>Couldn't read these {help.name} rules, so none can be added here until Tag can read them.</span></p>
      ) : listed.length === 0 ? (
        <p className="mcap">None yet.</p>
      ) : (
        <ul className="psaved" ref={list}>
          {listed.map(({ item, state }) => (
            <RuleRow key={item.kind + item.value} rule={item} state={state} busy={busy} going={busyRule === item.value}
              onRemove={() => void remove(item)} />
          ))}
        </ul>
      )}
      <form className="padd" onSubmit={(e) => { e.preventDefault(); void add(); }}>
        <div className="segc" role="radiogroup" aria-label={`New ${help.name} rule`}>
          {(["block", "allow"] as const).map((d) => (
            <button key={d} type="button" role="radio" aria-checked={decision === d} disabled={locked} onClick={() => setDecision(d)}>{DECISION[d]}</button>
          ))}
        </div>
        <input ref={input} className="field mono" value={text} placeholder={placeholder} aria-label={help.label} spellCheck={false}
          autoComplete="off" disabled={locked} onChange={(e) => setText(e.target.value)} />
        <button className="p-btn sm" type="submit" disabled={locked || !text.trim()}>{busyRule === "" ? <span className="spin" /> : "Add"}</button>
      </form>
      <p className="mcap">{help.hint} Applies from the next request.</p>
      {counted.length > 0 && (
        <>
          <button className="link pglobal" type="button" aria-expanded={showInherited} onClick={() => setShowInherited((v) => !v)}>
            <Icon name="chevdown" size={12} />
            Also follows {counted.map((g) => `${g.rules === null ? "unreadable" : g.rules.length} ${g.label}`).join(" and ")}
          </button>
          {showInherited && (
            <div className="pglist">
              {counted.map((group) => (
                <div key={group.label} className="pgroup">
                  <span className="pglab">{group.label[0].toUpperCase() + group.label.slice(1)} · {group.where}</span>
                  {group.rules === null
                    ? <p className="mwarn"><Icon name="warn" /><span>Couldn't read them.</span></p>
                    : <ul className="psaved global">{group.rules.map((rule) => <RuleRow key={rule.kind + rule.value + rule.decision} rule={rule} />)}</ul>}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
