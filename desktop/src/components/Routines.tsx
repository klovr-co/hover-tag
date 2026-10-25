// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// A Tag's routines: prompts it runs on a schedule and posts in one channel.
// Everything goes through `tag NAME routine … --json`, the same as the CLI,
// and saving here approves exactly what was saved.
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Bridge } from "../lib/bridge";
import { parseJSON, title, type TagRow } from "../lib/protocol";
import { useFresh, useListMotion } from "../lib/motion";
import {
  cliError, emptyForm, formArgs, formOf, formProblem, outcomeGood, outcomeText, routineLine, SCHEDULE_EXAMPLES, triggerText,
  localZone, matchZones, timeZones, when,
  type Routine, type RoutineDetail, type RoutineForm, type RoutineList, type RoutineRun, type RoutineRuns,
} from "../lib/routines";
import { generationTime } from "../lib/home";
import { ActivityArtifacts } from "./ActivityArtifacts";
import { ActivityDetails } from "./ActivityDetails";
import { ActivityTokens } from "./ActivityTokens";
import { ErrorLine, fitText, Icon, Switch } from "./ui";

type Say = (text: string) => void;

/** A Tag's routines. `focus` opens one (from its label in Activity); `showLogs` finds a failure in the Logs tab. */
export function Routines({ api, row, say, focus, showLogs }: {
  api: Bridge; row: TagRow; say: Say; focus?: string | null; showLogs?: (find: string) => void;
}) {
  const [list, setList] = useState<RoutineList | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<Routine | "new" | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const request = useRef(0);
  const load = useCallback(async () => {
    const current = ++request.current;
    const result = await api.tag([row.id, "routine", "list", "--json"]).catch(() => null);
    if (current !== request.current) return;
    if (!result || result.code !== 0) {
      setError(result ? cliError(result.stdout, result.stderr) : "Couldn't reach Tag to read its routines. Trying again every 30 seconds.");
      setList((l) => l ?? { tag_running: false, routines: [], limits: { per_tag: 10, per_channel: 3 } });
      return;
    }
    setList(parseJSON<RoutineList>(result.stdout));
  }, [api, row.id]);
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 30_000);
    return () => { request.current++; clearInterval(timer); };
  }, [load]);

  /** Run one routine command; on success reload and say so, on failure keep the message here. */
  const act = async (id: string, args: string[], done: string) => {
    setBusy(id);
    setError("");
    try {
      const result = await api.tag(args).catch(() => null);
      if (!result) { setError("Couldn't reach Tag. Try again."); return false; }
      if (result.code !== 0) { setError(cliError(result.stdout, result.stderr)); return false; }
      say(done);
      await load();
      return true;
    } finally {
      setBusy(null);
    }
  };
  const channels = row.channels ?? [];
  const focused = useRef<string | null>(null);
  useEffect(() => {
    if (!focus || !list || focused.current === focus || !list.routines.some((r) => r.id === focus)) return;
    focused.current = focus;
    setOpen(focus);
    requestAnimationFrame(() => document.querySelector(`[data-routine="${focus}"]`)?.scrollIntoView?.({ block: "nearest" }));
  }, [focus, list]);
  if (!list) return <p className="meta" style={{ margin: 0 }}>Loading routines…</p>;
  const full = list.routines.filter((r) => r.enabled).length >= list.limits.per_tag;
  return (
    <div className="rt">
      <p className="lead" style={{ margin: "0 0 8px" }}>
        {title(row)} runs these on its own and posts each result in one channel. Saving a routine approves exactly what you save.
      </p>
      {!list.tag_running && list.routines.length > 0 && (
        <p className="meta rt-note"><Icon name="warn" />{title(row)} is stopped, so its routines wait until it starts.</p>
      )}
      {list.tag_running && list.routines.length > 0 && list.scheduler?.problem && (
        <p className="meta rt-note"><Icon name="warn" />{list.scheduler.problem}</p>
      )}
      {error && <ErrorLine>{error}</ErrorLine>}
      <RoutineRows routines={list.routines} render={(item, state) => (
        <RoutineRow key={item.id} api={api} tag={row.id} routine={item} state={state} tagRunning={list.tag_running}
          open={open === item.id} toggleOpen={() => setOpen(open === item.id ? null : item.id)}
          busy={busy === item.id} saved={saved === item.id} act={act} showLogs={showLogs}
          edit={() => { setEditing(item); setOpen(null); }} />
      )} />
      {editing ? (
        <RoutineEditor key={editing === "new" ? "new" : editing.id} tag={row.id} channels={channels} routine={editing === "new" ? undefined : editing}
          busy={busy === "form"} cancel={() => setEditing(null)}
          save={async (args, name) => {
            const ok = await act("form", args, editing === "new" ? `Added ${name}` : `Saved ${name}`);
            if (ok) {
              setEditing(null);
              if (editing !== "new") {
                setSaved(editing.id);
                setTimeout(() => setSaved(null), 900);
              }
            }
            return ok;
          }} />
      ) : (
        <button className="ghost-add rt-add" disabled={full || !channels.length} onClick={() => { setEditing("new"); setError(""); }}>
          <span className="gi"><Icon name="plus" /></span>
          {!channels.length ? `Add ${title(row)} to a channel first` : full ? `${list.limits.per_tag} routines are running; pause one to add another` : "Add a routine"}
        </button>
      )}
      {!list.routines.length && !editing && (
        <p className="meta" style={{ margin: "8px 0 0" }}>
          For example, every weekday at 09:00, summarise yesterday's merged pull requests in #{channels[0]?.name ?? "a channel"}.
        </p>
      )}
    </div>
  );
}

/** Mounted once the list has loaded, so routines that were already there don't slide in on open. */
function RoutineRows({ routines, render }: { routines: Routine[]; render: (item: Routine, state: "stay" | "enter" | "leave") => ReactNode }) {
  const rows = useListMotion(routines, (r) => r.id);
  if (!rows.length) return null;
  return <div className="card rt-list">{rows.map(({ item, state }) => render(item, state))}</div>;
}

function RoutineRow({ api, tag, routine, state, tagRunning, open, toggleOpen, busy, saved, act, edit, showLogs }: {
  api: Bridge; tag: string; routine: Routine; state: "stay" | "enter" | "leave"; tagRunning: boolean; showLogs?: (find: string) => void;
  open: boolean; toggleOpen: () => void; busy: boolean; saved: boolean;
  act: (id: string, args: string[], done: string) => Promise<boolean>; edit: () => void;
}) {
  // A run starting or a new result gets a ripple; a saved edit flashes once. Opening the tab stays still.
  const ran = useFresh(routine.running || routine.last_run?.run_id, 900);
  const flash = saved;
  const [confirm, setConfirm] = useState(false);
  const cls = ["rt-row", state === "enter" ? "entering" : "", state === "leave" ? "leaving" : "", flash ? "fresh" : "", open ? "open" : ""].filter(Boolean).join(" ");
  const dot = routine.attention ? "bad" : routine.running ? "live" : routine.enabled ? "on" : "off";
  return (
    <div className={cls} data-routine={routine.id}>
      <div className="rt-head">
        <button className="rt-main" aria-expanded={open} onClick={toggleOpen}>
          <span className={`rt-dot ${dot}${ran ? " ran" : ""}`} aria-hidden="true" />
          <span className="rt-txt">
            <span className="rt-name">{routine.name}</span>
            <span className={routine.attention ? "rt-line bad" : "rt-line"}>{routineLine(routine, tagRunning)}</span>
          </span>
          <span className={open ? "chev turn" : "chev"}><Icon name="right" size={12} /></span>
        </button>
        <Switch on={routine.enabled} busy={busy} label={`${routine.enabled ? "Pause" : "Resume"} ${routine.name}`}
          onClick={() => void act(routine.id, [tag, "routine", routine.enabled ? "pause" : "resume", routine.id, "--json"],
            `${routine.enabled ? "Paused" : "Resumed"} ${routine.name}`)} />
      </div>
      {open && (
        <div className="rt-more">
          <RoutineHistory api={api} tag={tag} routine={routine} showLogs={showLogs} />
          <p className="rt-prompt selectable">{routine.prompt}</p>
          <p className="meta" style={{ margin: 0 }}>
            Up to ${routine.limits.max_budget_usd.toFixed(2)}, {routine.limits.max_turns} turns and {Math.round(routine.limits.timeout_seconds / 60)} minutes a run.
            {routine.catch_up === "skip" ? " Skips runs it missed." : " Runs once to catch up after this computer sleeps."}
          </p>
          <div className="rt-acts">
            <button className="p-btn soft sm" disabled={busy || !tagRunning || !routine.approved || routine.running}
              title={!tagRunning ? "Start the Tag first" : undefined}
              onClick={() => void act(routine.id, [tag, "routine", "run-now", routine.id, "--json"], `Running ${routine.name} now`)}>
              <Icon name="play" size={10} />Run now
            </button>
            <button className="p-btn soft sm" disabled={busy} onClick={edit}>{routine.approved ? "Edit" : "Review and approve"}</button>
            {confirm ? (
              <span className="rt-confirm">
                <span className="meta">Remove {routine.name} and its history?</span>
                <button className="p-btn quiet sm" onClick={() => setConfirm(false)}>Keep</button>
                <button className="p-btn soft sm" disabled={busy}
                  onClick={() => void act(routine.id, [tag, "routine", "remove", routine.id, "--json"], `Removed ${routine.name}`)}>Remove</button>
              </span>
            ) : (
              <button className="link bad rt-remove" onClick={() => setConfirm(true)}>Remove</button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

const PAGE = 20;

/** The latest five runs from `show`; "Show all runs" pages through `routine runs`. Each run opens what it did. */
function RoutineHistory({ api, tag, routine, showLogs }: { api: Bridge; tag: string; routine: Routine; showLogs?: (find: string) => void }) {
  const [detail, setDetail] = useState<RoutineDetail | null>(null);
  const [all, setAll] = useState<{ runs: RoutineRun[]; more: boolean } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const lastRun = routine.last_run?.run_id;
  useEffect(() => {
    let live = true;
    setAll(null);
    void api.tag([tag, "routine", "show", routine.id, "--json"]).then((result) => {
      if (live && result.code === 0) setDetail(parseJSON<RoutineDetail>(result.stdout));
    }, () => {});
    return () => { live = false; };
  }, [api, tag, routine.id, lastRun]);
  const loadRuns = async (offset: number) => {
    setLoading(true); setError("");
    try {
      const result = await api.tag([tag, "routine", "runs", routine.id, "--limit", String(PAGE), "--offset", String(offset), "--json"]).catch(() => null);
      if (!result || result.code !== 0) { setError(result ? cliError(result.stdout, result.stderr) : "Couldn't reach Tag. Try again."); return; }
      const page = parseJSON<RoutineRuns>(result.stdout);
      setAll((previous) => ({ runs: [...(offset ? previous?.runs ?? [] : []), ...page.runs], more: page.has_more }));
    } finally { setLoading(false); }
  };
  if (!detail) return null;
  const runs = all?.runs ?? detail.history.slice(0, 5);
  const more = all ? all.more : detail.history.length > 5;
  if (!runs.length) return <p className="meta" style={{ margin: 0 }}>No runs yet.</p>;
  // All runs scroll inside the routine, so a long history never pushes the rest of the tab away.
  const nearEnd = (list: HTMLElement) => list.scrollHeight - list.scrollTop - list.clientHeight < 60;
  return (
    <div className={all ? "rt-runs all" : "rt-runs"}>
      <ul className="rt-history" aria-label={`${routine.name} runs`}
        onScroll={all ? (e) => { if (all.more && !loading && nearEnd(e.currentTarget)) void loadRuns(all.runs.length); } : undefined}>
        {runs.map((run) => {
          const isOpen = open === run.run_id;
          return (
            <li key={run.run_id} className={`${outcomeGood(run) ? "good" : "bad"}${isOpen ? " open" : ""}`}>
              <button className="rt-run-head" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : run.run_id)}>
                <span className="rt-when">{when(run.started ?? run.scheduled_for, routine.timezone)}</span>
                <span className="rt-outcome">{outcomeText(run)}{triggerText(run)}</span>
                <span className={isOpen ? "chev turn" : "chev"}><Icon name="right" size={11} /></span>
              </button>
              {!isOpen && run.diagnostic && <span className="rt-diag selectable">{run.diagnostic}</span>}
              {isOpen && <RunDetail api={api} tag={tag} run={run} showLogs={showLogs} />}
            </li>
          );
        })}
      </ul>
      {error && <ErrorLine>{error}</ErrorLine>}
      <div className="rt-runs-foot">
        {more && <button className="link rt-all" disabled={loading} onClick={() => void loadRuns(all ? all.runs.length : 0)}>
          {loading ? "Loading runs…" : all ? "Load older runs" : `Show all ${detail.history.length} runs`}
        </button>}
        {all && <><span className="meta">{all.runs.length} of {detail.history.length} runs</span>
          <button className="link rt-all" onClick={() => { setAll(null); setOpen(null); }}>Show fewer</button></>}
      </div>
    </div>
  );
}

/** What one run did: the same reply, files, steps, tokens and timing as its entry in Activity. */
function RunDetail({ api, tag, run, showLogs }: { api: Bridge; tag: string; run: RoutineRun; showLogs?: (find: string) => void }) {
  const item = run.activity;
  const summary = item?.reply_summary?.trim() || item?.reply_preview?.trim();
  const facts = [
    item?.model_name || item?.model,
    item?.duration_seconds != null && item.duration_seconds > 0 ? generationTime(item.duration_seconds) : null,
    typeof run.cost_usd === "number" ? `$${run.cost_usd.toFixed(2)}` : null,
  ].filter(Boolean);
  return (
    <div className="rt-run">
      {summary && <p className="activity-reply-preview">{summary}</p>}
      {run.outcome === "no_reply" && <p className="meta">Nothing new to post, so it posted nothing.</p>}
      {item && <ActivityArtifacts api={api} item={item} />}
      {run.diagnostic && <p className="rt-diag selectable">{run.diagnostic}</p>}
      {(facts.length > 0 || item?.usage) && <p className="meta rt-run-facts">{facts.join(" · ")}{item?.usage && <ActivityTokens usage={item.usage} />}</p>}
      {(run.slack_url || (run.error_reference && showLogs)) && <div className="rt-run-acts">
        {run.slack_url && <button className="link" onClick={() => void api.open(run.slack_url!)}>Open in Slack <Icon name="external" size={12} /></button>}
        {run.error_reference && showLogs && <button className="link" onClick={() => showLogs(run.error_reference!)}>Find {run.error_reference} in logs</button>}
      </div>}
      {item ? <ActivityDetails api={api} tag={tag} item={item} label="Steps" />
        : run.activity_run_id && <p className="meta">Tag keeps steps for its latest 50 routine runs; this one's are gone.</p>}
    </div>
  );
}

function RoutineEditor({ tag, channels, routine, busy, cancel, save }: {
  tag: string; channels: { id: string; name: string | null }[]; routine?: Routine; busy: boolean;
  cancel: () => void; save: (args: string[], name: string) => Promise<boolean>;
}) {
  const [form, setForm] = useState<RoutineForm>(routine ? formOf(routine) : emptyForm(channels[0]?.id ?? ""));
  const [tried, setTried] = useState(false);
  const problem = formProblem(form);
  const set = (key: keyof RoutineForm) => (value: string) => setForm((f) => ({ ...f, [key]: value }));
  const submit = async () => {
    setTried(true);
    if (problem) return;
    await save(formArgs(tag, form, routine), form.name.trim());
  };
  return (
    <form className="card rt-form" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <label className="rt-field"><span>Name</span>
        <input className="field" autoFocus value={form.name} maxLength={80} placeholder="Weekly PR digest" onChange={(e) => set("name")(e.target.value)} />
      </label>
      <label className="rt-field"><span>When</span>
        <input className="field mono" value={form.schedule} aria-describedby="rt-when-help" onChange={(e) => set("schedule")(e.target.value)} />
      </label>
      <div className="rt-chips" id="rt-when-help">
        {SCHEDULE_EXAMPLES.map((example) => (
          <button type="button" key={example} className={form.schedule === example ? "rt-chip on" : "rt-chip"} onClick={() => set("schedule")(example)}>{example}</button>
        ))}
        <span className="meta">At most once an hour. Times on the hour start a few minutes after.</span>
      </div>
      <div className="rt-pair">
        <label className="rt-field"><span>Timezone</span>
          <TimezonePicker value={form.timezone} onChange={set("timezone")} />
        </label>
        <label className="rt-field"><span>Spend limit per run ($)</span>
          <input className="field" inputMode="decimal" value={form.budget} onChange={(e) => set("budget")(e.target.value)} />
        </label>
      </div>
      <label className="rt-field"><span>Post in</span>
        <select className="field" value={form.channel} onChange={(e) => set("channel")(e.target.value)}>
          {channels.map((c) => <option key={c.id} value={c.id}>#{c.name ?? c.id}</option>)}
        </select>
      </label>
      <label className="rt-field"><span>What to do each time</span>
        <textarea className="field" rows={3} ref={fitText} value={form.prompt} maxLength={8000}
          placeholder="Summarise the pull requests merged since the last run. Reply NO_REPLY if there are none."
          onChange={(e) => { set("prompt")(e.target.value); fitText(e.currentTarget); }} />
      </label>
      {tried && problem && <ErrorLine>{problem}</ErrorLine>}
      <div className="rt-acts end">
        <button type="button" className="p-btn quiet sm" onClick={cancel}>Cancel</button>
        <button type="submit" className="p-btn ink sm" disabled={busy}>{busy ? "Saving…" : routine ? "Save and approve" : "Add and approve"}</button>
      </div>
    </form>
  );
}

const MENU_HEIGHT = 340, MENU_MIN = 200, MENU_MIN_HEIGHT = 120, MENU_GAP = 12;

/** A timezone field you type into: it lists matching IANA zones and saves only one of them.
 *  A saved zone that this computer doesn't list is kept; the CLI checks every name anyway. */
function TimezonePicker({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const zones = useMemo(() => timeZones([value, localZone()]), [value]);
  const [query, setQuery] = useState<string | null>(null);
  const [active, setActive] = useState(0);
  const [fit, setFit] = useState({ above: false, height: MENU_HEIGHT });
  const wrap = useRef<HTMLDivElement>(null);
  const open = query !== null;
  const shown = open ? matchZones(zones, query) : [];
  // The list never leaves the visible part of the panel (or window): it opens below the field, or
  // above it when there's more room there, and is never taller than that room.
  const measure = useCallback(() => {
    const field = wrap.current?.getBoundingClientRect();
    if (!field) return;
    const view = wrap.current?.closest(".sl-body")?.getBoundingClientRect() ?? { top: 0, bottom: window.innerHeight };
    const below = Math.min(view.bottom, window.innerHeight) - field.bottom - MENU_GAP;
    const above = field.top - Math.max(view.top, 0) - MENU_GAP;
    const up = below < MENU_MIN && above > below;
    setFit({ above: up, height: Math.max(MENU_MIN_HEIGHT, Math.min(MENU_HEIGHT, up ? above : below)) });
  }, []);
  useEffect(() => {
    if (!open) return;
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [open, measure]);
  const pick = (zone: string) => { onChange(zone); setQuery(null); };
  const close = () => {
    const exact = query && zones.find((zone) => zone.toLowerCase() === query.trim().toLowerCase());
    if (exact) onChange(exact);
    setQuery(null);
  };
  return (
    <div className="mpick-wrap" ref={wrap}>
      <input className="field" role="combobox" aria-expanded={open} aria-controls="rt-zones" aria-autocomplete="list"
        aria-activedescendant={open && shown[active] ? `rt-zone-${active}` : undefined} spellCheck={false}
        value={open ? query : value} placeholder={localZone()}
        onFocus={(e) => { setQuery(""); setActive(0); e.currentTarget.select(); }}
        onChange={(e) => { setQuery(e.target.value); setActive(0); }}
        onBlur={close}
        onKeyDown={(e) => {
          if (!open) return;
          if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            e.preventDefault();
            setActive((i) => Math.max(0, Math.min(shown.length - 1, i + (e.key === "ArrowDown" ? 1 : -1))));
          } else if (e.key === "Enter" && shown[active]) {
            e.preventDefault();
            pick(shown[active]);
          } else if (e.key === "Escape") {
            e.preventDefault();
            e.stopPropagation();  // Close the list, not the editor.
            setQuery(null);
          }
        }} />
      {open && (
        <div className={fit.above ? "mmenu" : "mmenu below"} style={{ maxHeight: fit.height }} role="listbox" id="rt-zones" aria-label="Timezones">
          {shown.length ? shown.map((zone, index) => (
            <button key={zone} id={`rt-zone-${index}`} type="button" role="option" aria-selected={zone === value}
              className={index === active ? "mopt active" : "mopt"} onMouseDown={(e) => e.preventDefault()} onClick={() => pick(zone)}>
              <span className="mck">{zone === value && <Icon name="check" size={12} />}</span>{zone.replace(/_/g, " ")}
              {zone === localZone() && <span className="mdef">This computer</span>}
            </button>
          )) : <div className="mnote" role="status">No timezone matches “{query}”.</div>}
        </div>
      )}
    </div>
  );
}
