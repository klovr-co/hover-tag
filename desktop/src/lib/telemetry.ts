// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Usage telemetry: the same installation-wide choice and fixed events as the
// CLI. The app sends nothing itself; it asks `tag telemetry record` to queue
// one of the events below, and only after the person has seen the notice.
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { Bridge } from "./bridge";
import { parseJSON, type VersionInfo } from "./protocol";
import { trackStep, type SetupState } from "./setup";

export const USAGE_DATA_SUMMARY = "Tag collects minimal anonymous usage data to improve setup and reliability.";
export const USAGE_DATA_NEVER = "It never includes prompts, Slack messages, agent output, Tag or workspace names, paths, logs, credentials, or configuration values.";

/** `tag telemetry record` and the app events below. */
export const TELEMETRY_CAPABILITY = "telemetry-events";

export interface TelemetryStatus {
  enabled: boolean;
  /** Whether this build has an approved destination; older Tags leave it out. */
  available?: boolean;
  saved_preference: "on" | "off" | "not_set";
  process_override: "off" | null;
  privacy_notice: string | null;
}

export type SetupEntry = "first_tag" | "add_tag" | "finish_tag";
export type SetupStepName = "tag" | "ai" | "workspace" | "app" | "create" | "channels";

/** Every event the app can record, with exactly its fields. Tag checks each value again. */
export interface AppEvents {
  app_opened: { app_version: string };
  app_screen_viewed: { screen: "home" | "tag" | "settings" | "ai_settings" | "setup" };
  app_setup_started: { entry_point: SetupEntry };
  app_setup_step_completed: { step: SetupStepName; elapsed_seconds: number };
  app_setup_abandoned: { last_step: SetupStepName; reason: "cancelled" | "failed" | "ai_settings"; elapsed_seconds: number };
  app_setup_completed: { entry_point: SetupEntry; elapsed_seconds: number };
  app_update_finished: { outcome: "succeeded" | "failed" };
  app_channel_switched: { channel: "stable" | "beta" | "alpha" };
}

export type Track = <E extends keyof AppEvents>(event: E, fields: AppEvents[E]) => void;

export function recordArgs<E extends keyof AppEvents>(event: E, fields: AppEvents[E]): string[] {
  return ["telemetry", "record", event,
    ...Object.entries(fields).map(([name, value]) => `${name}=${typeof value === "number" ? Math.max(0, Math.round(value)) : value}`)];
}

/** Whether to show the first-run notice: collection is possible and nobody has chosen yet. */
export function needsNotice(status: TelemetryStatus | null): boolean {
  return !!status && status.available !== false && !!status.privacy_notice
    && status.saved_preference === "not_set" && !status.process_override;
}

export interface Telemetry {
  status: TelemetryStatus | null;
  /** The first status check finished, even if it failed. */
  loaded: boolean;
  /** Show the notice before anything else. */
  asking: boolean;
  /** Events are recorded: the person turned usage data on and this Tag accepts app events. */
  recording: boolean;
  /** Save the installation-wide choice; throws with Tag's message when it can't. */
  choose(on: boolean): Promise<void>;
  /** Close the notice for this run without saving a choice. Nothing is recorded. */
  dismiss(): void;
  /** Read the status and capabilities again, such as after an update. */
  reload(): void;
  track: Track;
}

export function useTelemetry(api: Bridge | null, installed: boolean): Telemetry {
  const [status, setStatus] = useState<TelemetryStatus | null>(null);
  const [canRecord, setCanRecord] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!api || !installed) return;
    let live = true;
    void Promise.all([
      api.tag(["telemetry", "status", "--json"]).then((r) => r.code === 0 ? parseJSON<TelemetryStatus>(r.stdout) : null).catch(() => null),
      api.tag(["version", "--json"]).then((r) => r.code === 0 && parseJSON<VersionInfo>(r.stdout).capabilities.includes(TELEMETRY_CAPABILITY)).catch(() => false),
    ]).then(([next, events]) => {
      if (!live) return;
      setStatus(next);
      setCanRecord(events);
      setLoaded(true);
    });
    return () => { live = false; };
  }, [api, installed, attempt]);

  const recording = !!api && canRecord && !!status?.enabled;
  const track = useCallback<Track>((event, fields) => {
    if (recording) void api!.tag(recordArgs(event, fields)).catch(() => {});
  }, [api, recording]);

  const choose = useCallback(async (on: boolean) => {
    if (!api) return;
    const result = await api.tag(["telemetry", on ? "on" : "off", "--json"]);
    if (result.code !== 0) throw new Error(result.stderr.trim().split("\n").pop() || "Tag couldn't save this choice.");
    setStatus(parseJSON<TelemetryStatus>(result.stdout));
  }, [api]);

  return {
    status, loaded: loaded || !installed, asking: !dismissed && needsNotice(status), recording, choose,
    dismiss: useCallback(() => setDismissed(true), []),
    reload: useCallback(() => setAttempt((value) => value + 1), []),
    track,
  };
}

export const TrackContext = createContext<Track>(() => {});
export const useTrack = () => useContext(TrackContext);

export function setupEntry(args: string[]): SetupEntry {
  return args[0] === "setup" ? "first_tag" : args[0] === "add" ? "add_tag" : "finish_tag";
}

const STEPS: SetupStepName[] = ["tag", "ai", "workspace", "create", "channels"];
const EXISTING_STEPS: SetupStepName[] = ["ai", "workspace", "app", "channels"];

/**
 * One setup attempt as start, step, completion, and abandonment events. It
 * reads only the step track, never answers, so nothing a person typed is sent.
 */
export class SetupTracker {
  private startedAt = 0;
  private step: SetupStepName | null = null;
  private index = 0;
  private since = 0;
  private finished = false;

  constructor(private track: Track, private entry: SetupEntry, private now: () => number = Date.now) {}

  start() {
    this.startedAt = this.since = this.now();
    this.track("app_setup_started", { entry_point: this.entry });
  }

  /** Call with every setup state; moving forward a step completes the one before it. */
  observe(state: SetupState) {
    if (this.finished) return;
    if (state.outcome === "complete") {
      this.completeStep();
      this.track("app_setup_completed", { entry_point: this.entry, elapsed_seconds: this.seconds(this.startedAt) });
      this.finished = true;
      return;
    }
    if (state.outcome) { this.abandon(state.outcome === "failed" ? "failed" : "cancelled"); return; }
    const steps = state.existing ? EXISTING_STEPS : STEPS;
    const index = Math.min(trackStep(state), steps.length - 1);
    const step = steps[index];
    if (step === this.step) return;
    if (this.step && index > this.index) this.completeStep();
    this.step = step;
    this.index = index;
    this.since = this.now();
  }

  abandon(reason: AppEvents["app_setup_abandoned"]["reason"]) {
    if (this.finished || !this.step) return;
    this.track("app_setup_abandoned", { last_step: this.step, reason, elapsed_seconds: this.seconds(this.startedAt) });
    this.finished = true;
  }

  private completeStep() {
    if (this.step) this.track("app_setup_step_completed", { step: this.step, elapsed_seconds: this.seconds(this.since) });
  }

  private seconds(from: number) {
    return (this.now() - from) / 1000;
  }
}
