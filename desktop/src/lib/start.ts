// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Starting a Tag as a few plain steps, from the readiness rows `tag NAME start --json` reports.
import { parseJSON } from "./protocol";

export type StepState = "pending" | "running" | "done" | "attention";

/** What a person sees while a Tag starts, in order. */
export const START_PHASES = [
  { id: "prepare", title: "Checking its Slack app" },
  { id: "memory", title: "Starting memory" },
  { id: "channels", title: "Reading its channels" },
  { id: "slack", title: "Connecting to Slack" },
] as const;

export type Phase = (typeof START_PHASES)[number]["id"];

export interface StartProgress {
  /** The phase each reported step belongs to, and how it went. */
  phases: Record<Phase, { state: StepState; text: string; background?: boolean }>;
  /** The phase running now, if any. */
  current: Phase | null;
}

export const initialStart = (): StartProgress => ({
  phases: Object.fromEntries(START_PHASES.map((p) => [p.id, { state: "pending", text: "" }])) as StartProgress["phases"],
  current: null,
});

/** Tag's readiness rows, grouped into the phases above. Unknown rows are setup work. */
export function phaseOf(step: string): Phase {
  if (step === "memory") return "memory";
  if (step === "channel-memory") return "channels";
  if (step === "checks" || step === "slack") return "slack";
  return "prepare";
}

interface ProgressLine { type: "progress"; step: string; state: "running" | "done" | "attention" | "info"; text: string }

/** Apply one line of `tag NAME start --json`; anything else leaves the progress as it was. */
export function startReducer(progress: StartProgress, line: string): StartProgress {
  let event: ProgressLine;
  try {
    event = parseJSON<ProgressLine>(line);
  } catch {
    return progress;
  }
  if (event?.type !== "progress" || typeof event.step !== "string") return progress;
  // Steps only move forward: a later row Tag doesn't group, such as the welcome DM, belongs to the latest step.
  const reached = Math.max(-1, ...START_PHASES.map((p, i) => (progress.phases[p.id].state === "pending" ? -1 : i)));
  const at = Math.max(START_PHASES.findIndex((p) => p.id === phaseOf(event.step)), reached);
  const phase = START_PHASES[at].id;
  const phases = { ...progress.phases };
  // Reaching a phase means the ones before it finished.
  START_PHASES.slice(0, at).forEach((p) => { if (phases[p.id].state !== "attention") phases[p.id] = { ...phases[p.id], state: "done" }; });
  const previous = phases[phase];
  const state: StepState = event.state === "attention" ? "attention"
    : event.state === "running" ? "running"
    : previous.state === "attention" ? "attention"
    // A finished row inside a phase that has more to do keeps it running until the next phase starts.
    : phase === "prepare" ? "running" : "done";
  if (event.state === "info" && previous.state === "done" && phase === "slack") return progress;
  // An informational row on a later step means it carries on in the background, such as a channel still importing.
  phases[phase] = { state, text: event.text, background: event.state === "info" && phase !== "prepare" };
  return { phases, current: state === "running" ? phase : progress.current === phase ? null : progress.current };
}

/** The outcome line of `tag NAME start --json`, or null for a progress line. */
export function startResult(line: string): { ok: boolean; error: string } | null {
  try {
    const event = parseJSON<{ type?: string; status?: string; error?: string; ok?: boolean }>(line);
    if (event?.type === "result") return { ok: event.status === "complete", error: event.error ?? "" };
    // A failure before progress began, such as an unknown Tag, uses the plain JSON error.
    if (event && event.ok === false) return { ok: false, error: event.error ?? "" };
  } catch {
    // Not JSON.
  }
  return null;
}
