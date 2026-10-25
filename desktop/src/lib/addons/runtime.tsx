// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Loads connected add-ons and fills Tag.app's fixed places with what they give.
// Add-only: an add-on adds screens, folders, buttons and switches; it never
// replaces a built-in screen, because no place here can.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { AppInfo, Bridge } from "../bridge";
import { makeHost, type AddonHost, type ChannelTarget, type Contributions, type Folder, type Mount, type TagTarget, type ThreadTarget } from "./host";
import { ManifestError, type ButtonPlace, type Manifest } from "./manifest";
import { configuredProviders, ConnectError, download, signIn, signOut, store, type Connection, type Provider } from "./providers";
import { verifyPackage } from "./signature";

/** Imports the checked entry module. Tests pass their own. */
export type ModuleLoader = (code: string) => Promise<{ activate?: (host: AddonHost) => Contributions | Promise<Contributions> }>;

const importModule: ModuleLoader = async (code) => {
  const url = URL.createObjectURL(new Blob([code], { type: "text/javascript" }));
  try { return await import(/* @vite-ignore */ url); } finally { URL.revokeObjectURL(url); }
};

export interface ProviderState {
  provider: Provider;
  status: "off" | "connecting" | "loading" | "on" | "error";
  error?: string;
  account?: { label: string };
  manifest?: Manifest;
  contributions?: Contributions;
}

export interface ScreenSlot { key: string; addon: string; title: string; icon: string; mount: Mount<Record<string, never>> }
export interface FolderSlot { key: string; addon: string; title: string; list: (t: TagTarget) => Promise<Folder[]>; mount: Mount<{ tag: string; folder: Folder }> }
export interface ButtonSlot { key: string; addon: string; label: string; place: ButtonPlace; run: (t: ThreadTarget | TagTarget) => void | Promise<void> }
export interface SwitchSlot { key: string; addon: string; label: string; detail?: string; get: (t: ChannelTarget) => Promise<boolean>; set: (t: ChannelTarget, on: boolean) => Promise<void> }

interface AddonsValue {
  providers: ProviderState[];
  connect(id: string): void;
  cancel(id: string): void;
  disconnect(id: string): void;
  openScreen(key: string): void;
  screens: ScreenSlot[];
  folders: FolderSlot[];
  buttons: ButtonSlot[];
  switches: SwitchSlot[];
  say(text: string): void;
}

const empty: AddonsValue = {
  providers: [], connect() {}, cancel() {}, disconnect() {}, openScreen() {}, say() {},
  screens: [], folders: [], buttons: [], switches: [],
};
const AddonsContext = createContext<AddonsValue>(empty);
export const useAddons = () => useContext(AddonsContext);

const message = (error: unknown) => error instanceof ManifestError || error instanceof ConnectError ? error.message
  : `The add-on didn't start: ${error instanceof Error ? error.message : String(error)}`;

export function AddonsProvider({ api, info, say, openScreen, tagIds, providers: given, loadModule = importModule, children }: {
  api: Bridge; info: AppInfo; say: (text: string) => void; openScreen: (key: string) => void; tagIds: string[];
  providers?: Provider[]; loadModule?: ModuleLoader; children: ReactNode;
}) {
  const providers = useMemo(() => given ?? configuredProviders(), [given]);
  const [states, setStates] = useState<Record<string, ProviderState>>({});
  const ids = useRef(tagIds);
  ids.current = tagIds;
  const aborts = useRef(new Map<string, AbortController>());
  const patch = useCallback((id: string, next: Omit<ProviderState, "provider">) =>
    setStates((all) => ({ ...all, [id]: { ...next, provider: providers.find((p) => p.id === id)! } })), [providers]);

  /** Checks a package and starts its code. Nothing runs before both checks pass. */
  const load = useCallback(async (provider: Provider, connection: Connection) => {
    const { manifest, code } = await verifyPackage(connection.package, provider.public_key, provider.id);
    const host = makeHost({
      manifest, account: connection.account, providerUrl: provider.url, token: connection.token,
      app: { version: info.version, platform: info.platform }, run: (args) => api.tag(args), tagIds: () => ids.current,
      open: (url) => api.open(url), say,
    });
    const module = await loadModule(code);
    if (typeof module.activate !== "function") throw new ManifestError("The add-on has no activate function.");
    const contributions = (await module.activate(host)) ?? {};
    return { manifest, contributions };
  }, [api, info.version, info.platform, loadModule, say]);

  const start = useCallback(async (provider: Provider, connection: Connection) => {
    patch(provider.id, { status: "loading", account: connection.account });
    try {
      const loaded = await load(provider, connection);
      patch(provider.id, { status: "on", account: connection.account, ...loaded });
      return loaded.manifest;
    } catch (error) {
      patch(provider.id, { status: "error", account: connection.account, error: message(error) });
      return null;
    }
  }, [load, patch]);

  // Connected add-ons load on start; then a newer signed version, if any, replaces the kept one.
  useEffect(() => {
    let live = true;
    for (const provider of providers) {
      const kept = store.get(provider.id);
      if (!kept) { patch(provider.id, { status: "off" }); continue; }
      void start(provider, kept).then(async () => {
        try {
          const latest = await download(provider, kept.token);
          if (!live || latest.manifest === kept.package.manifest) return;
          await verifyPackage(latest, provider.public_key, provider.id);
          const next = { ...kept, package: latest };
          store.set(provider.id, next);
          if (live) await start(provider, next);
        } catch { /* keep the version that loaded */ }
      });
    }
    return () => { live = false; };
  }, [providers, start, patch]);

  const connect = useCallback((id: string) => {
    const provider = providers.find((p) => p.id === id);
    if (!provider) return;
    aborts.current.get(id)?.abort();
    const abort = new AbortController();
    aborts.current.set(id, abort);
    patch(id, { status: "connecting" });
    void (async () => {
      try {
        const signedIn = await signIn(provider, { appVersion: info.version, open: (url) => api.open(url), signal: abort.signal });
        const pkg = await download(provider, signedIn.token);
        // Check before keeping it, so a bad download never replaces anything.
        await verifyPackage(pkg, provider.public_key, provider.id);
        const connection = { ...signedIn, package: pkg };
        store.set(id, connection);
        const manifest = await start(provider, connection);
        if (manifest) say(`${manifest.name} is ready`);
      } catch (error) {
        if (!abort.signal.aborted) patch(id, { status: store.get(id) ? "error" : "off", error: message(error) });
      }
    })();
  }, [providers, api, info.version, patch, start, say]);

  const cancel = useCallback((id: string) => {
    aborts.current.get(id)?.abort();
    patch(id, { status: "off" });
  }, [patch]);

  const disconnect = useCallback((id: string) => {
    const provider = providers.find((p) => p.id === id);
    const kept = store.get(id);
    store.set(id, null);
    patch(id, { status: "off" });
    if (provider && kept) void signOut(provider, kept.token);
  }, [providers, patch]);

  const value = useMemo<AddonsValue>(() => {
    const list = providers.map((p) => states[p.id] ?? { provider: p, status: "off" as const });
    const on = list.filter((s) => s.status === "on" && s.manifest && s.contributions);
    const fill = <E extends { id: string }, I, S>(pick: (m: Manifest) => E[], impl: (c: Contributions) => Record<string, I> | undefined,
      make: (addon: string, entry: E, i: I) => S) =>
      on.flatMap((s) => pick(s.manifest!).flatMap((entry) => {
        const i = impl(s.contributions!)?.[entry.id];
        return i ? [make(s.manifest!.id, entry, i)] : [];
      }));
    return {
      providers: list, connect, cancel, disconnect, openScreen, say,
      screens: fill((m) => m.screens, (c) => c.screens, (addon, e, i) => ({ key: `${addon}/${e.id}`, addon, title: e.title, icon: e.icon, mount: i.mount })),
      folders: fill((m) => m.knowledge_folders, (c) => c.knowledge_folders, (addon, e, i) => ({ key: `${addon}/${e.id}`, addon, title: e.title, list: i.list, mount: i.mount })),
      buttons: fill((m) => m.buttons, (c) => c.buttons, (addon, e, i) => ({ key: `${addon}/${e.id}`, addon, label: e.label, place: e.place, run: i.run })),
      switches: fill((m) => m.channel_switches, (c) => c.channel_switches, (addon, e, i) => ({ key: `${addon}/${e.id}`, addon, label: e.label, detail: e.detail, get: i.get, set: i.set })),
    };
  }, [providers, states, connect, cancel, disconnect, openScreen, say]);

  return <AddonsContext.Provider value={value}>{children}</AddonsContext.Provider>;
}

/** Hosts an add-on's own drawing; a failure shows in place of it instead of breaking the app. */
export function AddonMount<T>({ mount, context, className = "addon-mount" }: { mount: Mount<T>; context: T; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState("");
  const key = JSON.stringify(context);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    setFailed("");
    let cleanup: void | (() => void);
    try { cleanup = mount(element, JSON.parse(key) as T); }
    catch (error) { setFailed(error instanceof Error ? error.message : String(error)); }
    return () => {
      try { cleanup?.(); } catch { /* the add-on's own clean-up */ }
      element.replaceChildren();
    };
  }, [mount, key]);
  return <>{failed && <p className="meta bad" role="alert">This add-on screen stopped: {failed}</p>}<div ref={ref} className={className} /></>;
}
