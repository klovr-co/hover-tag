// One product update: respect the CLI's saved channel/pin and verify both parts.
import type { Bridge } from "./bridge";
import { parseJSON } from "./protocol";
import { failureLine } from "./tags";

interface Upgrade {
  ok: boolean;
  status: string;
  current?: { version?: string };
  target?: { version?: string };
}
export interface ProductUpdate {
  version: string;
  current: string;
  runtime: boolean;
  desktop: boolean;
}

export async function checkUpdate(api: Bridge, appVersion: string): Promise<ProductUpdate> {
  const [result, app] = await Promise.all([
    api.tag(["upgrade", "--dry-run", "--json"]), api.checkAppUpdate(),
  ]);
  if (result.code !== 0) throw new Error(failureLine(result, "Couldn't check for updates."));
  const upgrade = parseJSON<Upgrade>(result.stdout);
  if (!upgrade.ok || !["available", "current", "pinned", "ahead"].includes(upgrade.status)) {
    throw new Error("Couldn't determine the Tag update. Please try again.");
  }
  const current = upgrade.current?.version;
  const version = upgrade.status === "available" ? upgrade.target?.version : current;
  if (!current || !version) throw new Error("The update check did not return a Tag version.");
  const desktop = version !== appVersion;
  if (desktop && app?.version !== version) {
    throw new Error("A complete Tag update isn't available for your release channel yet. Check again later. Your installed version has been kept.");
  }
  return { version, current, runtime: version !== current, desktop };
}

export async function installUpdate(api: Bridge, appVersion: string): Promise<ProductUpdate> {
  // Recheck on every attempt, including retries after a partial installation.
  const update = await checkUpdate(api, appVersion);
  if (update.runtime) {
    const result = await api.tag(["upgrade", "--json"]);
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
  return { ...update, current: update.version, runtime: false, desktop: false };
}
