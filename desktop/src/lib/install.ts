// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Install progress from the installer's structured "@tag-progress" lines.
import { cleanLog, INSTALL_STEPS, parseProgressLine, stepIndex } from "./protocol";

export type StepState = "pending" | "running" | "done" | "failed";

export interface InstallState {
  current: number; // index into INSTALL_STEPS; -1 before the first event
  states: StepState[];
  log: string;
  version: string;
  command: string;
  failure: string;
  finished: boolean;
}

export const initialInstall: InstallState = {
  current: 0,
  states: INSTALL_STEPS.map((_, i) => (i === 0 ? "running" : "pending")),
  log: "", version: "", command: "", failure: "", finished: false,
};

export type InstallAction = { type: "line"; line: string } | { type: "exit"; code: number };

function advance(state: InstallState, index: number): InstallState {
  if (index <= state.current && state.current >= 0) return state;
  return {
    ...state, current: index,
    states: INSTALL_STEPS.map((_, i) => (i < index ? "done" : i === index ? "running" : "pending")),
  };
}

export function installReducer(state: InstallState, action: InstallAction): InstallState {
  if (action.type === "exit") {
    if (action.code === 0) {
      return { ...state, finished: true, states: INSTALL_STEPS.map(() => "done") };
    }
    const states = [...state.states];
    if (state.current >= 0) states[state.current] = "failed";
    return { ...state, states, failure: state.failure || `The installer stopped (exit ${action.code}).` };
  }
  const event = parseProgressLine(action.line);
  if (!event) {
    const text = cleanLog(action.line);
    const failed = /Installation failed: (.*)/.exec(text);
    return { ...state, log: state.log + text + "\n", failure: failed ? failed[1] : state.failure };
  }
  let next = state;
  if (event.version) next = { ...next, version: event.version };
  if (event.step === "done") return { ...next, command: event.command ?? "" };
  if (event.step === "failed") return { ...next, failure: event.message ?? "Installation failed." };
  const index = stepIndex(event.step);
  return index >= 0 ? advance(next, index) : next;
}

/** Overall progress: finished steps, plus a creeping share of the running one. */
export function fraction(state: InstallState, runningFor: number): number {
  if (state.finished) return 1;
  const base = INSTALL_STEPS.slice(0, Math.max(state.current, 0)).reduce((sum, s) => sum + s.weight, 0);
  const step = INSTALL_STEPS[state.current];
  if (!step || state.failure) return base;
  // Approach 90% of the step so long downloads never look frozen.
  return base + step.weight * 0.9 * (1 - Math.exp(-runningFor / 25));
}
