// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Add-on providers: who this build trusts, how Tag.app connects to one, and
// where a connected add-on is kept. See docs/reference/addon-interface.md.
import type { AddonPackage } from "./signature";

/** One trusted provider, configured at build time; Settings shows one "Connect …" line for each. */
export interface Provider {
  id: string;
  /** Shown as "Connect {name}". */
  name: string;
  description?: string;
  /** Base address of the provider's connect API. */
  url: string;
  /** Base64 Ed25519 public key (32 raw bytes) that its add-on must be signed with. */
  public_key: string;
}

/** Reads `VITE_TAG_ADDON_PROVIDERS`, a JSON list of providers. Bad entries are left out. */
export function parseProviders(source: string | undefined): Provider[] {
  if (!source?.trim()) return [];
  let list: unknown;
  try { list = JSON.parse(source); } catch { return []; }
  if (!Array.isArray(list)) return [];
  return list.filter((p): p is Provider => !!p && typeof p === "object"
    && typeof p.id === "string" && /^[a-z0-9][a-z0-9-]{0,40}$/.test(p.id)
    && typeof p.name === "string" && !!p.name.trim()
    && typeof p.url === "string" && /^https?:\/\//.test(p.url)
    && typeof p.public_key === "string" && !!p.public_key);
}

export const configuredProviders = () => parseProviders(import.meta.env.VITE_TAG_ADDON_PROVIDERS as string | undefined);

/** A connected provider: its token, who signed in, and the last verified package. */
export interface Connection {
  token: string;
  account: { label: string };
  package: AddonPackage;
}

const STORE = "tag.addons.v1";

export const store = {
  all(): Record<string, Connection> {
    try { return JSON.parse(localStorage.getItem(STORE) ?? "{}") ?? {}; } catch { return {}; }
  },
  get(id: string): Connection | null { return this.all()[id] ?? null; },
  set(id: string, connection: Connection | null) {
    const all = this.all();
    if (connection) all[id] = connection; else delete all[id];
    localStorage.setItem(STORE, JSON.stringify(all));
  },
};

export class ConnectError extends Error {}

const api = (provider: Provider, path: string) => provider.url.replace(/\/+$/, "") + "/tag-addon/v1" + path;

async function post<T>(provider: Provider, path: string, body: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(api(provider, path), { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  } catch {
    throw new ConnectError(`Couldn't reach ${provider.name}. Check your connection and try again.`);
  }
  if (!response.ok) throw new ConnectError(`${provider.name} couldn't connect (${response.status}). Try again.`);
  return response.json() as Promise<T>;
}

/** Downloads the provider's current add-on package with a connection's token. */
export async function download(provider: Provider, token: string): Promise<AddonPackage> {
  let response: Response;
  try { response = await fetch(api(provider, "/package"), { headers: { authorization: `Bearer ${token}` } }); }
  catch { throw new ConnectError(`Couldn't reach ${provider.name} to download its add-on.`); }
  if (response.status === 401) throw new ConnectError(`${provider.name} signed this computer out. Connect again.`);
  if (!response.ok) throw new ConnectError(`${provider.name} couldn't send its add-on (${response.status}).`);
  return response.json() as Promise<AddonPackage>;
}

interface Started { device_code: string; verification_url: string; interval?: number; expires_in?: number }
type Polled = { status: "pending" } | { status: "approved"; token: string; account: { label: string } } | { status: "denied" | "expired" };

/**
 * Signs in once through the browser: the provider gives a code and a page, the
 * person approves this computer on that page, and Tag.app waits for the answer.
 */
export async function signIn(provider: Provider, options: {
  appVersion: string; open: (url: string) => Promise<void>; signal: AbortSignal; sleep?: (ms: number) => Promise<void>;
}): Promise<{ token: string; account: { label: string } }> {
  const sleep = options.sleep ?? ((ms: number) => new Promise((resolve) => setTimeout(resolve, ms)));
  const started = await post<Started>(provider, "/connect", { client: "tag-app", app_version: options.appVersion, interfaces: [1] });
  if (!/^https?:\/\//.test(started.verification_url ?? "")) throw new ConnectError(`${provider.name} sent a sign-in page Tag.app can't open.`);
  await options.open(started.verification_url);
  const deadline = Date.now() + Math.min(started.expires_in ?? 600, 1800) * 1000;
  const every = Math.max(1, Math.min(started.interval ?? 2, 10)) * 1000;
  while (Date.now() < deadline) {
    await sleep(every);
    if (options.signal.aborted) throw new ConnectError("Connecting was cancelled.");
    const polled = await post<Polled>(provider, "/connect/token", { device_code: started.device_code });
    if (polled.status === "approved") return { token: polled.token, account: polled.account };
    if (polled.status === "denied") throw new ConnectError(`Signing in to ${provider.name} was declined in the browser.`);
    if (polled.status === "expired") break;
  }
  throw new ConnectError("Signing in took too long. Try again.");
}

/** Tells the provider this computer signed out. Best effort: the local copy is removed either way. */
export async function signOut(provider: Provider, token: string) {
  try { await fetch(api(provider, "/disconnect"), { method: "POST", headers: { authorization: `Bearer ${token}` } }); } catch { /* offline */ }
}
