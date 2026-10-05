// One product update: adopt the app's initial channel, then respect saved choices.
import type { Bridge } from "./bridge";
import { parseJSON } from "./protocol";
import { failureLine } from "./tags";

interface Upgrade {
  ok: boolean;
  status: string;
  current?: { version?: string; channel?: string | null; selection?: string };
  target?: { version?: string; channel?: string | null };
}

/** Release channels Tag.app can follow; each has its own signed Tag.app feed. */
export const APP_CHANNELS = ["stable", "beta", "alpha"] as const;
export type Channel = (typeof APP_CHANNELS)[number];
export const isAppChannel = (value: unknown): value is Channel => APP_CHANNELS.includes(value as Channel);
export const publishedChannel = (version: string): Channel => version.includes("-alpha") ? "alpha" : version.includes("-beta") ? "beta" : "stable";

export interface ProductUpdate {
  version: string;
  current: string;
  runtime: boolean;
  desktop: boolean;
  /** The channel these updates come from: the saved one, or the one being switched to. */
  channel: string | null;
  /** Tag is pinned to an exact version and follows no channel. */
  pinned: boolean;
  /** The channel's newest release is older than the installed one, so Tag waits for it to catch up. */
  ahead: boolean;
  /** A newer release whose Tag.app builds aren't published yet; Tag stays on `current` until they are. */
  preparing?: string;
  /** Adopt the app's default through the shared CLI policy on the next upgrade. */
  initializeChannel?: boolean;
}

const EDGE = "Tag.app doesn't follow the edge channel. Choose Stable, Beta, or Alpha in Settings to update the app and your Tags together.";

/** What updating would do. With `channel`, previews switching to it; nothing is saved. */
export async function checkUpdate(api: Bridge, appVersion: string, channel?: Channel): Promise<ProductUpdate> {
  const info = await api.info();
  const needsInitialization = info.channelInitialized === false;
  // Inspect the old policy first so exact pins and edge are never silently replaced.
  if (needsInitialization && !channel) {
    const result = await api.tag(["upgrade", "--dry-run", "--json"]);
    if (result.code !== 0) throw new Error(failureLine(result, "Couldn't check for updates."));
    const policy = parseJSON<Upgrade>(result.stdout);
    if (policy.ok && policy.current?.selection === "channel" && isAppChannel(policy.current.channel)) {
      const initial = publishedChannel(appVersion);
      // Already following the app's channel: nothing to adopt, so complete the migration now.
      if (policy.current.channel === initial) await api.markChannelInitialized();
      else channel = initial;
    }
  }
  const result = await api.tag(["upgrade", ...(channel ? ["--channel", channel] : []), "--dry-run", "--json"]);
  if (result.code !== 0) throw new Error(failureLine(result, "Couldn't check for updates."));
  const upgrade = parseJSON<Upgrade>(result.stdout);
  if (!upgrade.ok || !["available", "current", "pinned", "ahead"].includes(upgrade.status)) {
    throw new Error("Couldn't determine the Tag update. Please try again.");
  }
  const current = upgrade.current?.version;
  const version = upgrade.status === "available" ? upgrade.target?.version : current;
  if (!current || !version) throw new Error("The update check did not return a Tag version.");
  const pinned = upgrade.status === "pinned";
  const following = channel ?? (pinned ? null : upgrade.target?.channel ?? upgrade.current?.channel ?? null);
  const update = { version, current, runtime: version !== current, channel: following, pinned, ahead: upgrade.status === "ahead",
    ...(needsInitialization && channel ? { initializeChannel: true } : {}) };
  const desktop = version !== appVersion;
  if (!desktop) return { ...update, desktop };
  if (following === "edge") throw new Error(EDGE);
  // The app follows the same channel as the runtime, so both land on one version.
  const app = await api.checkAppUpdate(isAppChannel(following) ? following : undefined);
  if (app?.version !== version) {
    // The channel moved before its Tag.app builds were attached. Nothing is wrong
    // with this installation: report it as current and offer the update once ready.
    if (update.runtime && current === appVersion) {
      return { ...update, version: current, runtime: false, desktop: false, preparing: version };
    }
    throw new Error("A complete Tag update isn't available for your release channel yet. Check again later. Your installed version has been kept.");
  }
  return { ...update, desktop };
}

export const preparingText = (version: string) =>
  `Tag ${version} is still being prepared for the app. Check again in a few minutes.`;

/** What an update is doing: your Tags first, then the app restarts if it changed. */
export type UpdatePhase = "runtime" | "app";

/** Update the app and runtime together. With `channel`, also switches to it and saves the choice. */
export async function installUpdate(api: Bridge, appVersion: string, channel?: Channel,
  onPhase?: (phase: UpdatePhase) => void): Promise<ProductUpdate> {
  // Recheck on every attempt, including retries after a partial installation.
  const update = await checkUpdate(api, appVersion, channel);
  const requested = channel ?? (update.initializeChannel && isAppChannel(update.channel) ? update.channel : undefined);
  // Switching would move your Tags to a release the app can't follow yet.
  if (requested && update.preparing) throw new Error(preparingText(update.preparing));
  // Switching runs even when nothing installs, so the CLI saves the new channel.
  if (update.runtime || requested) {
    onPhase?.("runtime");
    const result = await api.tag(["upgrade", ...(requested ? ["--channel", requested] : []), "--json"]);
    if (result.code !== 0) throw new Error(failureLine(result, "Couldn't finish updating Tag. Try again to continue."));
    const upgraded = parseJSON<Upgrade>(result.stdout);
    if (!upgraded.ok) throw new Error("Couldn't finish updating Tag. Try again to continue.");
  }
  // Never restart into a different product release if the channel moved mid-update.
  const result = await api.tag(["version", "--json"]);
  if (result.code !== 0 || parseJSON<{ version: string }>(result.stdout).version !== update.version) {
    throw new Error("Tag changed during the update. Check again to finish updating.");
  }
  if (requested) {
    // Read without an override: verify the policy really persisted before completing
    // migration v1. A failed write/verification remains retryable on the next run.
    const policy = await api.tag(["upgrade", "--dry-run", "--json"]);
    const saved = policy.code === 0 ? parseJSON<Upgrade>(policy.stdout) : null;
    if (!saved?.ok || saved.current?.channel !== requested || saved.current?.selection !== "channel") {
      throw new Error("Couldn't verify the saved release channel. Try again to finish updating Tag.");
    }
    await api.markChannelInitialized();
  }
  if (update.desktop) {
    onPhase?.("app");
    await api.installAppUpdate(update.version);
  }
  return { ...update, current: update.version, runtime: false, desktop: false, initializeChannel: false, pinned: requested ? false : update.pinned };
}

// ---- One update, shared by Home and Settings ------------------------------------

export type UpdateStatus = "idle" | "checking" | "available" | "current" | "updating" | "done" | "failed";

export interface UpdateState {
  status: UpdateStatus;
  /** What the last check found; kept while updating so notices can name the version. */
  update: ProductUpdate | null;
  phase: UpdatePhase | null;
  error: string;
}

export const initialUpdate: UpdateState = { status: "idle", update: null, phase: null, error: "" };

export type UpdateAction =
  | { type: "checking" }
  | { type: "checked"; update: ProductUpdate }
  | { type: "updating" }
  | { type: "phase"; phase: UpdatePhase }
  | { type: "updated"; update: ProductUpdate }
  /** Checking didn't work, for example on a Tag the installer doesn't manage; nothing was changed. */
  | { type: "checkFailed"; error: string }
  /** An update that started didn't finish. */
  | { type: "failed"; error: string }
  | { type: "settled" };

export function updateReducer(state: UpdateState, action: UpdateAction): UpdateState {
  switch (action.type) {
    case "checking":
      // A check never interrupts an update in progress.
      return state.status === "updating" ? state : { ...state, status: "checking", error: "" };
    case "checked": {
      if (state.status === "updating") return state;
      const available = action.update.runtime || action.update.desktop || action.update.initializeChannel;
      return { ...state, status: available ? "available" : "current", update: action.update, error: "" };
    }
    case "updating":
      return { ...state, status: "updating", phase: null, error: "" };
    case "phase":
      return { ...state, phase: action.phase };
    case "updated":
      return { status: "done", update: action.update, phase: null, error: "" };
    case "checkFailed":
      // Only an update that started can be unfinished; a check never is.
      return state.status === "checking" ? { ...state, status: "idle", error: action.error } : state;
    case "failed":
      // No installation began if the preflight check never reported a phase.
      return { ...state, status: state.phase ? "failed" : "idle", phase: null, error: action.error };
    case "settled":
      return state.status === "done" ? { ...state, status: "current" } : state;
  }
}

/** The version an update is going to, for notices. */
export const targetVersion = (state: UpdateState) => state.update?.version ?? "";
