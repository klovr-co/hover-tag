// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Routines: a saved prompt a Tag runs on a schedule and posts in one channel.
// The app only reads and changes them through `tag NAME routine … --json`.
import routineListExample from "../../../protocol/examples/routine-list.json";
import { parseJSON } from "./protocol";

export type RunOutcome = "posted" | "no_reply" | "failed" | "budget_exceeded" | "timeout" | "skipped" | "missed" | "interrupted" | "blocked" | "cancelled";

export interface RoutineRun {
  run_id: string;
  trigger: "schedule" | "catch-up" | "run-now" | "retry" | string;
  scheduled_for: string | null;
  started: string | null;
  ended: string | null;
  outcome: RunOutcome | string;
  diagnostic?: string;
  count?: number;
  cost_usd?: number;
  tokens?: number;
  tools_used?: string[];
}

export interface Routine {
  id: string;
  name: string;
  schedule: string;
  timezone: string;
  channel: string;
  channel_name: string | null;
  prompt: string;
  owner: string;
  enabled: boolean;
  paused_reason: string | null;
  catch_up: "latest" | "skip";
  limits: { max_turns: number; max_budget_usd: number; timeout_seconds: number; max_tokens: number };
  approved: boolean;
  running: boolean;
  next_run: string | null;
  last_run: RoutineRun | null;
  consecutive_failures: number;
  attention: boolean;
}

/** Whether the running Tag is checking its routines; ``problem`` says what is wrong when it isn't. */
export interface SchedulerStatus { ok: boolean; problem: string | null; last_check: string | null }
export interface RoutineList {
  tag_running: boolean; scheduler?: SchedulerStatus; routines: Routine[]; limits: { per_tag: number; per_channel: number };
}
export interface RoutineDetail { routine: Routine; upcoming: string[]; history: RoutineRun[] }

/** What someone fills in to add or change a routine. */
export interface RoutineForm { name: string; schedule: string; timezone: string; channel: string; prompt: string; budget: string }

export const SCHEDULE_EXAMPLES = ["weekdays 09:00", "mon 09:00", "every 2h", "monthly 1 09:00"];

const OUTCOME: Record<string, string> = {
  posted: "Posted", no_reply: "Nothing new", failed: "Failed", budget_exceeded: "Stopped at its spend limit",
  timeout: "Stopped at its time limit", skipped: "Skipped", missed: "Missed", interrupted: "Interrupted", blocked: "Needs approval",
  cancelled: "Stopped",
};
const GOOD = new Set(["posted", "no_reply", "cancelled"]);
const TRIGGER: Record<string, string> = { "run-now": " · run by hand", "catch-up": " · caught up", retry: " · tried again" };

/** How a run started, when it wasn't simply on time. */
export const triggerText = (run: RoutineRun) => TRIGGER[run.trigger] ?? "";

export const outcomeText = (run: RoutineRun) =>
  (OUTCOME[run.outcome] ?? "Needs attention") + (run.outcome === "missed" && run.count && run.count > 1 ? ` ${run.count} runs` : "");
export const outcomeGood = (run: RoutineRun) => GOOD.has(run.outcome);

/** This computer's timezone, as the CLI expects it. */
export const localZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

/** Common zones, for a webview that can't list them all. The CLI still checks any name. */
const FALLBACK_ZONES = [
  "UTC", "Africa/Cairo", "Africa/Johannesburg", "Africa/Lagos", "America/Chicago", "America/Denver", "America/Los_Angeles",
  "America/Mexico_City", "America/New_York", "America/Sao_Paulo", "America/Toronto", "Asia/Dubai", "Asia/Hong_Kong",
  "Asia/Kolkata", "Asia/Seoul", "Asia/Shanghai", "Asia/Singapore", "Asia/Tokyo", "Australia/Sydney", "Europe/Amsterdam",
  "Europe/Berlin", "Europe/London", "Europe/Madrid", "Europe/Paris", "Pacific/Auckland",
];

/** Every IANA zone this computer knows, plus any given (the saved one, this computer's), sorted. */
export function timeZones(keep: string[] = []): string[] {
  let zones: string[] = [];
  try {
    zones = (Intl as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.("timeZone") ?? [];
  } catch { zones = []; }
  return [...new Set([...(zones.length ? zones : FALLBACK_ZONES), "UTC", ...keep.filter(Boolean)])].sort();
}

/** Zones matching what someone typed: "new york", "new_york" and "America/New" all find America/New_York. */
export function matchZones(zones: string[], query: string, limit = 60): string[] {
  const words = query.toLowerCase().replace(/[_/]/g, " ").split(/\s+/).filter(Boolean);
  if (!words.length) return zones.slice(0, limit);
  const hits = zones.filter((zone) => {
    const name = zone.toLowerCase().replace(/[_/]/g, " ");
    return words.every((word) => name.includes(word));
  });
  // Zones whose city starts with the text come first, shorter names before longer:
  // "lon" finds Europe/London before Arctic/Longyearbyen and America/Kentucky/Louisville.
  const city = (zone: string) => (zone.split("/").pop() ?? zone).toLowerCase().replace(/_/g, " ");
  const rank = (zone: string) => (city(zone).startsWith(words[0]) ? 0 : 1000) + city(zone).length;
  return hits.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b)).slice(0, limit);
}

/** "Mon 19 Oct, 09:03" in the routine's own timezone, with the zone when it isn't this computer's. */
export function when(iso: string | null, zone: string, now = new Date()) {
  if (!iso) return "";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  const sameYear = at.getFullYear() === now.getFullYear();
  const text = new Intl.DateTimeFormat(undefined, {
    weekday: "short", day: "numeric", month: "short", ...(sameYear ? {} : { year: "numeric" }),
    hour: "2-digit", minute: "2-digit", timeZone: zone,
  }).format(at);
  return zone === localZone() ? text : `${text} (${zone})`;
}

/** The one line under a routine's name. */
export function routineLine(routine: Routine, tagRunning: boolean) {
  const where = routine.channel_name ? `#${routine.channel_name}` : routine.channel;
  if (!routine.approved) return `${routine.schedule} · ${where} · changed since it was approved`;
  if (routine.paused_reason) return `${routine.schedule} · ${where} · ${routine.paused_reason}`;
  if (!routine.enabled) return `${routine.schedule} · ${where} · paused`;
  if (routine.running) return `${routine.schedule} · ${where} · running now`;
  if (!tagRunning) return `${routine.schedule} · ${where} · waits for the Tag to start`;
  return `${routine.schedule} · ${where} · next ${when(routine.next_run, routine.timezone)}`;
}

export const emptyForm = (channel = ""): RoutineForm =>
  ({ name: "", schedule: SCHEDULE_EXAMPLES[0], timezone: localZone(), channel, prompt: "", budget: "0.50" });

export const formOf = (routine: Routine): RoutineForm => ({
  name: routine.name, schedule: routine.schedule, timezone: routine.timezone, channel: routine.channel,
  prompt: routine.prompt, budget: routine.limits.max_budget_usd.toFixed(2),
});

/** CLI arguments to add a routine, or to save only what changed in one. */
export function formArgs(tag: string, form: RoutineForm, previous?: Routine): string[] {
  const before = previous ? formOf(previous) : null;
  // An unapproved routine is approved by saving it, even when nothing changed.
  const resend = !!previous && !previous.approved;
  const changed = (key: keyof RoutineForm) => resend || !before || form[key].trim() !== before[key].trim();
  const args = previous ? [tag, "routine", "edit", previous.id] : [tag, "routine", "add"];
  if (changed("name")) args.push("--name", form.name.trim());
  if (changed("schedule")) args.push("--schedule", form.schedule.trim());
  if (changed("timezone")) args.push("--timezone", form.timezone.trim());
  if (changed("channel")) args.push("--channel", form.channel);
  if (changed("prompt")) args.push("--prompt", form.prompt.trim());
  if (changed("budget") && form.budget.trim()) args.push("--max-budget-usd", form.budget.trim());
  return [...args, "--via", "tag-app", "--json"];
}

/** What's missing before Save can work; the CLI checks everything else. */
export function formProblem(form: RoutineForm): string {
  if (!form.name.trim()) return "Give the routine a name.";
  if (!form.schedule.trim()) return "Choose when it runs.";
  if (!form.channel) return "Choose a channel.";
  if (!form.prompt.trim()) return "Say what the Tag should do each time.";
  const budget = Number(form.budget);
  if (form.budget.trim() && !(budget >= 0.01 && budget <= 20)) return "The spend limit must be between $0.01 and $20.";
  return "";
}

/** The CLI's own message from a failed --json command. */
export function cliError(stdout: string, stderr: string) {
  try {
    const parsed = parseJSON<{ error?: string }>(stdout);
    if (parsed.error) return parsed.error;
  } catch { /* fall through to stderr */ }
  return stderr.trim().split("\n").pop()?.replace(/^Error:\s*/, "") || "Tag couldn't save the routine.";
}

// ---- Sample data ---------------------------------------------------------------

/** The next time it is `weekday` (0 = Sunday) at hour:minute in `zone`, after now. */
export function nextWeekly(weekday: number, hour: number, minute: number, zone: string, now = new Date()): Date {
  const parts = (at: Date) => Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    timeZone: zone, hourCycle: "h23", weekday: "short", year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric",
  }).formatToParts(at).map((p) => [p.type, p.value]));
  for (let ahead = 0; ahead <= 8; ahead++) {
    const date = parts(new Date(now.getTime() + ahead * 86_400_000));
    const wall = Date.UTC(Number(date.year), Number(date.month) - 1, Number(date.day), hour, minute);
    // The zone's offset at that wall time, from what the zone shows at the same instant read as UTC.
    const shown = parts(new Date(wall));
    const offset = Date.UTC(Number(shown.year), Number(shown.month) - 1, Number(shown.day), Number(shown.hour), Number(shown.minute)) - wall;
    const at = new Date(wall - offset);
    if (["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(parts(at).weekday) === weekday && at > now) return at;
  }
  return new Date(now.getTime() + 7 * 86_400_000);
}

/** A tiny in-memory stand-in for `tag NAME routine`, for the demo app and tests. */
export function demoRoutines() {
  const byTag = new Map<string, Routine[]>();
  const history = new Map<string, RoutineRun[]>();
  const sample = structuredClone(routineListExample.routines[0]) as unknown as Routine;
  // The example's dates are fixed; the demo moves them around now, so "next" is never in the past.
  const day = 86_400_000;
  const next = nextWeekly(1, 9, 3, sample.timezone);
  const last = new Date(next.getTime() - 7 * day);
  sample.next_run = next.toISOString();
  if (sample.last_run) {
    sample.last_run = { ...sample.last_run, scheduled_for: last.toISOString(), started: new Date(last.getTime() + 4_000).toISOString(),
      ended: new Date(last.getTime() + 97_000).toISOString() };
  }
  const listFor = (tag: string, first: boolean) => {
    if (!byTag.has(tag)) byTag.set(tag, first ? [structuredClone(sample)] : []);
    return byTag.get(tag)!;
  };
  const option = (args: string[], name: string) => args.includes(name) ? args[args.indexOf(name) + 1] : undefined;
  const fail = (error: string) => ({ ok: false as const, error });
  return (tag: string, first: boolean, running: boolean, args: string[]) => {
    const routines = listFor(tag, first);
    const [action, reference] = args;
    const find = () => routines.find((r) => r.id === reference || r.name.toLowerCase() === (reference ?? "").toLowerCase());
    if (action === "list") return { ok: true, tag_running: running, scheduler: routineListExample.scheduler, routines, invalid: 0, limits: routineListExample.limits };
    if (action === "add") {
      const name = option(args, "--name") ?? "";
      if (routines.some((r) => r.name.toLowerCase() === name.toLowerCase())) return fail(`A routine named '${name}' already exists`);
      const schedule = option(args, "--schedule") ?? "";
      if (/^every\s+\d+m$/.test(schedule) && Number(schedule.replace(/\D/g, "")) < 60) return fail("Routines run at most once an hour");
      const routine: Routine = {
        ...structuredClone(sample), id: `r-${Math.random().toString(16).slice(2, 12).padEnd(10, "0")}`, name, schedule,
        timezone: option(args, "--timezone") ?? localZone(), channel: option(args, "--channel") ?? "", channel_name: null,
        prompt: option(args, "--prompt") ?? "", last_run: null, running: false,
        limits: { ...sample.limits, max_budget_usd: Number(option(args, "--max-budget-usd") ?? sample.limits.max_budget_usd) },
      };
      routines.push(routine);
      return { ok: true, tag_running: running, routine };
    }
    const routine = find();
    if (!routine) return fail(`No routine named '${reference}'.`);
    if (action === "show") {
      const next = routine.next_run ? [routine.next_run] : [];
      return { ok: true, tag_running: running, routine, upcoming: next, history: history.get(routine.id) ?? (routine.last_run ? [routine.last_run] : []) };
    }
    if (action === "edit") {
      for (const [flag, key] of [["--name", "name"], ["--schedule", "schedule"], ["--timezone", "timezone"], ["--channel", "channel"], ["--prompt", "prompt"]] as const) {
        const value = option(args, flag);
        if (value !== undefined) routine[key] = value;
      }
      const budget = option(args, "--max-budget-usd");
      if (budget) routine.limits = { ...routine.limits, max_budget_usd: Number(budget) };
      routine.approved = true;
      return { ok: true, tag_running: running, routine };
    }
    if (action === "pause" || action === "resume") {
      routine.enabled = action === "resume";
      routine.paused_reason = null;
      routine.next_run = routine.enabled ? new Date(Date.now() + 86_400_000).toISOString() : null;
      return { ok: true, tag_running: running, routine };
    }
    if (action === "remove") {
      routines.splice(routines.indexOf(routine), 1);
      return { ok: true, removed: routine.id };
    }
    if (action === "run-now") {
      if (!running) return fail("Start the Tag first");
      const run: RoutineRun = { run_id: Math.random().toString(16).slice(2), trigger: "run-now", scheduled_for: null,
        started: new Date().toISOString(), ended: new Date().toISOString(), outcome: "posted" };
      history.set(routine.id, [run, ...(history.get(routine.id) ?? [])]);
      routine.last_run = run;
      return { ok: true, requested: routine.id };
    }
    return fail(`Unknown routine command ${action}`);
  };
}
