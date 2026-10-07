// PROTOTYPE — throwaway, do not ship. Issue #185: how should a session's full conversation open?
// Three variants of the Tag activity tab, switchable via ?activity=A|B|C (←/→ also cycle).
//   A  Inline expand: sessions expand in the feed; sticky heading collapses.
//   B  Side panel: feed of session rows; conversation opens in a right panel (slide-over under 900px).
//   C  Drill-in: session list replaces the feed; conversation opens full width with a back arrow.
import { useEffect, useState } from "react";
import { Avatar, tagIcon } from "./ui";
import type { TagRow } from "../lib/protocol";

type Round = { at: string; ask: string; reply: string; state?: "working" | "failed"; steps: number; model: string; place: string };
type Session = { id: string; title: string; place: string; rounds: Round[] };

const SESSIONS: Session[] = [
  { id: "s1", title: "Close finished hover-tag issues, leave hover-zulip alone", place: "#prod-hover", rounds: [
    { at: "16:58", ask: "Which hover-tag issues are already done?", reply: "Seven issues have merged PRs. I listed them with links.", steps: 6, model: "Opus 5.5 med", place: "#prod-hover" },
    { at: "17:20", ask: "Close those, and don't touch hover-zulip", reply: "Closed all seven with PR links in the closing comment.", steps: 9, model: "Opus 5.5 med", place: "#prod-hover" },
    { at: "17:33", ask: "Double-check nothing in hover-zulip changed", reply: "Left hover-zulip untouched, and today's hover-tag closures are still in place with PR links.", steps: 1, model: "Opus 5.5 med", place: "#prod-hover" },
  ] },
  { id: "s2", title: "Make the empty space at the bottom of Home feel less empty", place: "#prod-hover", rounds: [
    { at: "18:40", ask: "Screenshot Home with one Tag", reply: "Here's Home with one Tag in light and dark.", steps: 4, model: "Opus 5.5 med", place: "#prod-hover" },
    { at: "18:52", ask: "Why is the bottom so empty?", reply: "The card list ends early and the hint row is hidden with one Tag.", steps: 3, model: "Opus 5.5 med", place: "#prod-hover" },
    { at: "19:05", ask: "Try a 'Try it in Slack' row there", reply: "Added it under the list. Screenshot attached.", steps: 12, model: "Opus 5.5 med", place: "#prod-hover" },
    { at: "19:12", ask: "I like how that looks but some times down there feel quite empty", reply: "Working in #prod-hover…", state: "working", steps: 9, model: "Opus 5.5 med", place: "#prod-hover" },
  ] },
  { id: "s3", title: "Draft the launch checklist from launch-plan.md", place: "DM", rounds: [
    { at: "19:20", ask: "Read launch-plan.md and create launch-checklist.md", reply: "Created launch-checklist.md with 14 items grouped by owner.", steps: 5, model: "GPT-5.5 med", place: "DM" },
  ] },
];

const ME = { name: "Maxine", initial: "M" };

const css = `
.pr-wrap { position: relative; display: flex; flex: 1; min-height: 0; gap: 0; }
.pr-feed { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.pr-session { border: 1px solid var(--line); border-radius: 10px; background: var(--paper-2); }
.pr-session[data-active="true"] { border-color: var(--link); box-shadow: 0 0 0 1px var(--link); }
.pr-head { position: sticky; top: -12px; z-index: 2; display: flex; align-items: center; gap: 8px; width: 100%; text-align: left; padding: 8px 10px; background: var(--paper-2); border-radius: 10px 10px 0 0; font-weight: 700; color: var(--ink); font-size: 13.5px; }
.pr-head:hover { background: var(--canvas); }
.pr-head .pr-meta { margin-left: auto; font-weight: 400; color: var(--muted); font-size: 12px; white-space: nowrap; }
.pr-chev { color: var(--muted); width: 12px; flex: none; transition: transform .15s; }
.pr-chev[data-open="true"] { transform: rotate(90deg); }
.pr-body { padding: 0 6px 6px; }
.pr-msg { display: flex; gap: 10px; padding: 5px 4px; border-radius: 8px; }
.pr-msg .who { font-weight: 700; color: var(--ink); font-size: 14px; }
.pr-msg .who span { font-weight: 400; color: var(--muted); font-size: 12px; margin-left: 6px; }
.pr-msg p { margin: 1px 0 0; font-size: 14px; }
.pr-msg > div:not(.pr-me) { min-width: 0; flex: 1; }
.pr-msg img, .pr-me { width: 30px; height: 30px; border-radius: 8px; flex: none; }
.pr-me { flex: 0 0 30px; display: grid; place-items: center; background: #c98bb9; color: #fff; font-weight: 700; font-size: 13px; }
.pr-more { font-size: 12px; color: var(--link); font-weight: 600; padding: 2px 4px 2px 44px; text-align: left; }
.pr-chan { color: var(--link); font-weight: 600; }
.pr-row { display: block; width: 100%; text-align: left; border-radius: 10px; padding: 8px 10px; border: 1px solid var(--line); background: var(--paper-2); }
.pr-row:hover { background: var(--canvas); }
.pr-row[data-active="true"] { border-color: var(--link); background: var(--sky-soft); }
.pr-row .t { display: flex; gap: 8px; font-weight: 700; color: var(--ink); font-size: 13.5px; }
.pr-row .t span { margin-left: auto; font-weight: 400; color: var(--muted); font-size: 12px; white-space: nowrap; }
.pr-row .l { color: var(--text); font-size: 13px; margin-top: 3px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.pr-row .l b { color: var(--ink); }
.pr-line { display: flex; align-items: center; gap: 7px; margin-top: 5px; font-size: 13.5px; color: var(--text); min-width: 0; }
.pr-line span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pr-line.muted { color: var(--muted); }
.pr-line img { width: 18px; height: 18px; border-radius: 5px; flex: none; }
.pr-me.sm { flex: 0 0 18px; width: 18px; height: 18px; border-radius: 5px; font-size: 10px; }
.pr-row .t .pr-dot { margin: 6px 0 0; flex: none; }
.pr-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--amber, #e0a020); margin-right: 6px; vertical-align: 1px; }
.app.pr-extended { width: 1240px; }
.pr-row .s { color: var(--muted); font-size: 12px; margin-top: 3px; }
.pr-panel { flex: 0 0 440px; border-left: 1px solid var(--line); margin: -12px -16px -16px 12px; padding: 12px 14px; background: var(--paper); display: flex; flex-direction: column; overflow: auto; }
.pr-panel-h { display: flex; align-items: center; gap: 8px; padding-bottom: 8px; margin-bottom: 6px; border-bottom: 1px solid var(--line); font-weight: 700; color: var(--ink); font-size: 14px; }
.pr-panel-h .x { margin-left: auto; color: var(--muted); border-radius: 6px; width: 26px; height: 26px; display: grid; place-items: center; font-size: 15px; }
.pr-panel-h .x:hover { background: var(--fill); color: var(--ink); }
.pr-panel-h .back { color: var(--link); font-size: 16px; border-radius: 6px; width: 26px; height: 26px; display: grid; place-items: center; }
.pr-panel-h .back:hover { background: var(--fill); }
.pr-round { border-top: 1px dashed var(--line); padding-top: 4px; margin-top: 4px; }
.pr-round:first-of-type { border-top: 0; }
.pr-narrow .pr-panel { position: absolute; inset: -12px -16px -16px -16px; width: auto; margin: 0; border-left: 0; z-index: 5; box-shadow: -8px 0 24px rgba(0,0,0,.08); }
.pr-bar { position: fixed; left: 50%; bottom: 14px; transform: translateX(-50%); z-index: 99; display: flex; align-items: center; gap: 4px; background: #111; color: #fff; border-radius: 999px; padding: 4px 6px; font: 600 12px system-ui; box-shadow: 0 6px 20px rgba(0,0,0,.3); }
.pr-bar button { color: #fff; width: 26px; height: 26px; border-radius: 999px; }
.pr-bar button:hover { background: #333; }
.pr-bar span { padding: 0 8px; white-space: nowrap; }
`;

function Ask({ r }: { r: Round }) {
  return <div className="pr-msg"><div className="pr-me">{ME.initial}</div><div>
    <div className="who">{ME.name}<span>{r.at}</span></div><p>{r.ask}</p></div></div>;
}
function Reply({ row, r }: { row: TagRow; r: Round }) {
  const name = row.slack_name ?? "Tag";
  return <div className="pr-msg"><Avatar row={row} size={30} badge={false} className="" /><div>
    <div className="who">{name}<span>{r.at}</span><span>· {r.model}</span><span>· {r.steps} steps ›</span></div>
    <p style={r.state === "working" ? { color: "var(--muted)" } : undefined}>{r.reply}</p></div></div>;
}
function Conversation({ row, s }: { row: TagRow; s: Session }) {
  return <>{s.rounds.map((r, i) => <div key={i} className="pr-round"><Ask r={r} /><Reply row={row} r={r} /></div>)}</>;
}
function last(s: Session) { return s.rounds[s.rounds.length - 1]; }
function rowStatus(s: Session) {
  const r = last(s);
  return `${s.rounds.length} ${s.rounds.length === 1 ? "round" : "rounds"} · ${r.state === "working" ? "Working…" : "Replied"} in ${s.place} · ${r.at}`;
}

// A — inline expand
function VariantA({ row }: { row: TagRow }) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen({}); };
    addEventListener("keydown", key); return () => removeEventListener("keydown", key);
  }, []);
  return <div className="pr-feed">{SESSIONS.map((s) => {
    const isOpen = !!open[s.id];
    const earlier = s.rounds.slice(0, -1);
    const toggle = () => setOpen((all) => ({ ...all, [s.id]: !all[s.id] }));
    return <div key={s.id} className="pr-session">
      <button className="pr-head" aria-expanded={isOpen} onClick={toggle}>
        <span className="pr-chev" data-open={isOpen}>▸</span>{s.title}<span className="pr-meta">{s.place}</span></button>
      <div className="pr-body">
        {earlier.length > 0 && <button className="pr-more" onClick={toggle}>{isOpen ? "Hide" : "Show"} {earlier.length} earlier {earlier.length === 1 ? "round" : "rounds"}</button>}
        {isOpen && earlier.map((r, i) => <div key={i} className="pr-round"><Ask r={r} /><Reply row={row} r={r} /></div>)}
        <div className="pr-round"><Ask r={last(s)} /><Reply row={row} r={last(s)} /></div>
      </div>
    </div>;
  })}</div>;
}

function useNarrow() {
  const [narrow, setNarrow] = useState(innerWidth < 1240);
  useEffect(() => { const f = () => setNarrow(innerWidth < 1240); addEventListener("resize", f); return () => removeEventListener("resize", f); }, []);
  return narrow;
}
function useSelection(onEmpty: boolean) {
  const [active, setActive] = useState<string | null>(null);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") setActive(null);
      if (active && (e.key === "ArrowDown" || e.key === "ArrowUp") && !e.shiftKey) {
        e.preventDefault();
        const i = SESSIONS.findIndex((s) => s.id === active);
        const next = SESSIONS[Math.min(SESSIONS.length - 1, Math.max(0, i + (e.key === "ArrowDown" ? 1 : -1)))];
        setActive(next.id);
      }
    };
    addEventListener("keydown", key); return () => removeEventListener("keydown", key);
  }, [active, onEmpty]);
  return [active, setActive] as const;
}
function SessionRow({ row, s, active, onClick }: { row: TagRow; s: Session; active: boolean; onClick: () => void }) {
  const r = last(s);
  const working = r.state === "working";
  // Two lines: what the conversation is about, then where it stands now.
  return <button className="pr-row" data-active={active} onClick={onClick}>
    <div className="t">{working && <i className="pr-dot" aria-label="Working" />}{s.title}
      <span>{s.place}{s.rounds.length > 1 && ` · ${s.rounds.length}`} · {r.at}</span></div>
    <div className="pr-line muted"><Avatar row={row} size={18} badge={false} className="" /><span>{working ? "Working on it…" : r.reply}</span></div>
  </button>;
}

// B — side panel (slide-over when narrow)
function VariantB({ row }: { row: TagRow }) {
  const narrow = useNarrow();
  const [active, setActive] = useSelection(false);
  const s = SESSIONS.find((s) => s.id === active);
  useEffect(() => {
    const app = document.querySelector(".app");
    app?.classList.toggle("pr-extended", !!s && !narrow);
    return () => app?.classList.remove("pr-extended");
  }, [s, narrow]);
  return <div className={`pr-wrap${narrow ? " pr-narrow" : ""}`}>
    <div className="pr-feed">{SESSIONS.map((x) => <SessionRow key={x.id} row={row} s={x} active={x.id === active} onClick={() => setActive(active === x.id ? null : x.id)} />)}</div>
    {s && <aside className="pr-panel" aria-label="Conversation">
      <div className="pr-panel-h">{narrow && <button className="back" aria-label="Back" onClick={() => setActive(null)}>←</button>}
        <span>{s.title}</span>{!narrow && <button className="x" aria-label="Close" onClick={() => setActive(null)}>✕</button>}</div>
      <Conversation row={row} s={s} />
    </aside>}
  </div>;
}

// C — drill-in (full width, always)
function VariantC({ row }: { row: TagRow }) {
  const [active, setActive] = useSelection(false);
  const s = SESSIONS.find((s) => s.id === active);
  if (s) return <div className="pr-feed">
    <div className="pr-panel-h"><button className="back" aria-label="Back" onClick={() => setActive(null)}>←</button>
      <span>{s.title}</span><span style={{ marginLeft: "auto", fontWeight: 400, color: "var(--muted)", fontSize: 12 }}>{rowStatus(s)}</span></div>
    <Conversation row={row} s={s} />
  </div>;
  return <div className="pr-feed">{SESSIONS.map((x) => <SessionRow key={x.id} row={row} s={x} active={false} onClick={() => setActive(x.id)} />)}</div>;
}

const VARIANTS = [
  ["A", "Inline expand", VariantA],
  ["B", "Side panel", VariantB],
  ["C", "Drill-in", VariantC],
] as const;

export function prototypeVariant() {
  return new URLSearchParams(location.search).get("activity");
}

export function ActivityPrototype({ row }: { row: TagRow }) {
  const [key, setKey] = useState(prototypeVariant() ?? "A");
  const index = Math.max(0, VARIANTS.findIndex(([k]) => k === key));
  const go = (step: number) => {
    const next = VARIANTS[(index + step + VARIANTS.length) % VARIANTS.length][0];
    const url = new URL(location.href); url.searchParams.set("activity", next); history.replaceState(null, "", url);
    setKey(next);
  };
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, textarea, [contenteditable]")) return;
      if (e.key === "ArrowLeft") go(-1); if (e.key === "ArrowRight") go(1);
    };
    addEventListener("keydown", key); return () => removeEventListener("keydown", key);
  });
  const [, name, View] = VARIANTS[index];
  return <>
    <style>{css}</style>
    <View row={row} />
    {import.meta.env.DEV && <div className="pr-bar"><button onClick={() => go(-1)}>‹</button><span>{key} ({name})</span><button onClick={() => go(1)}>›</button></div>}
    <img src={tagIcon} alt="" hidden />
  </>;
}
