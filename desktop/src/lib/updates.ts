// One product update: respect the CLI's saved channel/pin and verify both parts.
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
}

const EDGE = "Tag.app doesn't follow the edge channel. Choose Stable, Beta, or Alpha in Settings to update the app and your Tags together.";

/** What updating would do. With `channel`, previews switching to it; nothing is saved. */
export async function checkUpdate(api: Bridge, appVersion: string, channel?: Channel): Promise<ProductUpdate> {
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
  const update = { version, current, runtime: version !== current, channel: following, pinned, ahead: upgrade.status === "ahead" };
  const desktop = version !== appVersion;
  if (!desktop) return { ...update, desktop };
  if (following === "edge") throw new Error(EDGE);
  // The app follows the same channel as the runtime, so both land on one version.
  const app = await api.checkAppUpdate(isAppChannel(following) ? following : undefined);
  if (app?.version !== version) {
    throw new Error("A complete Tag update isn't available for your release channel yet. Check again later. Your installed version has been kept.");
  }
  return { ...update, desktop };
}

/** Update the app and runtime together. With `channel`, also switches to it and saves the choice. */
export async function installUpdate(api: Bridge, appVersion: string, channel?: Channel): Promise<ProductUpdate> {
  // Recheck on every attempt, including retries after a partial installation.
  const update = await checkUpdate(api, appVersion, channel);
  // Switching runs even when nothing installs, so the CLI saves the new channel.
  if (update.runtime || channel) {
    const result = await api.tag(["upgrade", ...(channel ? ["--channel", channel] : []), "--json"]);
    if (result.code !== 0) throw new Error(failureLine(result, "Couldn't finish updating Tag. Try again to continue."));
    const upgraded = parseJSON<Upgrade>(result.stdout);
    if (!upgraded.ok) throw new Error("Couldn't finish updating Tag. Try again to continue.");
  }
  // Never restart into a different product release if the channel moved mid-update.
  const result = await api.tag(["version", "--json"]);
  if (result.code !== 0 || parseJSON<{ version: string }>(result.stdout).version !== update.version) {
    throw new Error("Tag changed during the update. Check again to finish updating.");
  }
  if (update.desktop) await api.installAppUpdate(update.version);
  return { ...update, current: update.version, runtime: false, desktop: false, pinned: channel ? false : update.pinned };
}
