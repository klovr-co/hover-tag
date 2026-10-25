// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AppInfo, Bridge } from "../bridge";
import { AddonConnections, AddonChannelSwitches, AddonNav, AddonTagActions } from "../../components/Addons";
import { allowedTagCall, makeHost, type Contributions } from "./host";
import { parseManifest } from "./manifest";
import { parseProviders, signIn, store, type Provider } from "./providers";
import { AddonsProvider } from "./runtime";
import { SIGNED_PREFIX, sha256, verifyPackage, type AddonPackage } from "./signature";
import example from "../../../../protocol/examples/addon-manifest.json";

const CODE = "export function activate(host) { return {}; }";

const manifest = async (overrides: Record<string, unknown> = {}, code = CODE) => JSON.stringify({
  interface: 1, id: "example", name: "Example", version: "1.0.0", publisher: "acme",
  entry_sha256: await sha256(code), tag_commands: ["list", "threads"],
  screens: [{ id: "reports", title: "Reports", icon: "📊" }],
  knowledge_folders: [{ id: "shared", title: "Shared" }],
  buttons: [{ id: "open-web", place: "thread.actions", label: "Open on the web" }],
  channel_switches: [{ id: "sync", label: "Sync", detail: "Copies this channel's Tag answers." }],
  ...overrides,
});

const b64 = (bytes: ArrayBuffer) => btoa(String.fromCharCode(...new Uint8Array(bytes)));

async function keys() {
  const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]) as CryptoKeyPair;
  return { pair, publicKey: b64(await crypto.subtle.exportKey("raw", pair.publicKey)) };
}

async function signed(pair: CryptoKeyPair, text: string, code = CODE): Promise<AddonPackage> {
  const signature = await crypto.subtle.sign({ name: "Ed25519" }, pair.privateKey, new TextEncoder().encode(SIGNED_PREFIX + text));
  return { format: "tag-addon/1", manifest: text, code, signature: b64(signature) };
}

describe("manifest", () => {
  it("reads the places an add-on fills", async () => {
    const m = parseManifest(await manifest(), "acme");
    expect(m.screens[0]).toEqual({ id: "reports", title: "Reports", icon: "📊" });
    expect(m.buttons[0].place).toBe("thread.actions");
    expect(m.channel_switches[0].detail).toMatch(/Copies/);
  });

  it("reads the documented example", () => {
    expect(parseManifest(JSON.stringify(example), "example").buttons[0].place).toBe("thread.actions");
  });

  it("refuses a newer interface version with an update hint", async () => {
    const text = await manifest({ interface: 2 });
    expect(() => parseManifest(text, "acme")).toThrow(/newer Tag.app/);
  });

  it("refuses places this version doesn't have, so nothing can replace a built-in screen", async () => {
    expect(() => parseManifest(manifestSync({ replaces: ["home"] }), "acme")).toThrow(/places this Tag.app doesn't have: replaces/);
    expect(() => parseManifest(manifestSync({ buttons: [{ id: "x", place: "settings.header", label: "X" }] }), "acme")).toThrow(/unknown place/);
  });

  it("refuses a manifest from another publisher", async () => {
    expect(() => parseManifest(manifestSync(), "someone-else")).toThrow(/different publisher/);
  });
});

/** A manifest without awaiting the checksum, for checks that fail before it matters. */
function manifestSync(overrides: Record<string, unknown> = {}) {
  return JSON.stringify({ interface: 1, id: "example", name: "Example", version: "1.0.0", publisher: "acme",
    entry_sha256: "0".repeat(64), tag_commands: [], ...overrides });
}

describe("signature", () => {
  it("loads a package signed by the configured key", async () => {
    const { pair, publicKey } = await keys();
    const result = await verifyPackage(await signed(pair, await manifest()), publicKey, "acme");
    expect(result.manifest.id).toBe("example");
    expect(result.code).toBe(CODE);
  });

  it("refuses another key, a changed manifest and changed code", async () => {
    const { pair, publicKey } = await keys();
    const other = await keys();
    const pkg = await signed(pair, await manifest());
    await expect(verifyPackage(pkg, other.publicKey, "acme")).rejects.toThrow(/isn't signed/);
    await expect(verifyPackage({ ...pkg, manifest: pkg.manifest.replace("Example", "Evil") }, publicKey, "acme")).rejects.toThrow(/isn't signed/);
    await expect(verifyPackage({ ...pkg, code: CODE + "\nfetch('x')" }, publicKey, "acme")).rejects.toThrow(/doesn't match/);
  });

  it("fails closed without a usable key", async () => {
    const { pair } = await keys();
    await expect(verifyPackage(await signed(pair, await manifest()), "bm90IGEga2V5", "acme")).rejects.toThrow(/can't check/);
  });
});

describe("host", () => {
  it("runs only listed tag commands, always as --json", () => {
    const m = { tag_commands: ["list", "threads"] };
    expect(allowedTagCall(["list", "--json"], m, [])).toBe(true);
    expect(allowedTagCall(["maya", "threads", "--json", "--limit", "5"], m, ["maya"])).toBe(true);
    expect(allowedTagCall(["list"], m, [])).toBe(false);
    expect(allowedTagCall(["maya", "remove", "--json"], m, ["maya"])).toBe(false);
    expect(allowedTagCall(["--json"], m, [])).toBe(false);
  });

  it("refuses other commands without running them, and adds the token to provider requests", async () => {
    const run = vi.fn().mockResolvedValue({ code: 0, stdout: "{}", stderr: "" });
    const fetched = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}"));
    const host = makeHost({ manifest: parseManifest(await manifest(), "acme"), account: { label: "Maya" }, providerUrl: "https://addons.example/",
      token: "t0k", app: { version: "1", platform: "linux" }, run, tagIds: () => [], open: vi.fn(), say: vi.fn() });
    expect((await host.tag(["upgrade", "--json"])).code).toBe(2);
    expect(run).not.toHaveBeenCalled();
    await host.request("/api/x");
    expect(fetched.mock.calls[0][0]).toBe("https://addons.example/api/x");
    expect(new Headers(fetched.mock.calls[0][1]!.headers).get("authorization")).toBe("Bearer t0k");
    await expect(host.request("https://elsewhere.example/")).rejects.toThrow();
    await expect(host.open("file:///etc/passwd")).rejects.toThrow();
  });
});

describe("providers", () => {
  it("keeps only complete provider entries", () => {
    const list = parseProviders(JSON.stringify([
      { id: "acme", name: "Acme", url: "https://acme.example", public_key: "k" },
      { id: "Bad Id", name: "X", url: "https://x", public_key: "k" },
      { id: "nourl", name: "X", url: "ftp://x", public_key: "k" },
    ]));
    expect(list.map((p) => p.id)).toEqual(["acme"]);
    expect(parseProviders("not json")).toEqual([]);
  });

  it("signs in through the browser and waits for approval", async () => {
    const replies = [
      { device_code: "d1", verification_url: "https://acme.example/approve?code=d1", interval: 1 },
      { status: "pending" }, { status: "approved", token: "tok", account: { label: "Maya" } },
    ];
    const fetched = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(replies.shift())));
    const open = vi.fn().mockResolvedValue(undefined);
    const result = await signIn(PROVIDER, { appVersion: "1", open, signal: new AbortController().signal, sleep: async () => {} });
    expect(open).toHaveBeenCalledWith("https://acme.example/approve?code=d1");
    expect(result).toEqual({ token: "tok", account: { label: "Maya" } });
    expect(fetched.mock.calls.map((c) => String(c[0]))).toEqual([
      "https://acme.example/tag-addon/v1/connect", "https://acme.example/tag-addon/v1/connect/token", "https://acme.example/tag-addon/v1/connect/token"]);
  });

  it("stops when the person declines", async () => {
    const replies = [{ device_code: "d1", verification_url: "https://acme.example/a" }, { status: "denied" }];
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(replies.shift())));
    await expect(signIn(PROVIDER, { appVersion: "1", open: vi.fn(), signal: new AbortController().signal, sleep: async () => {} }))
      .rejects.toThrow(/declined/);
  });
});

const PROVIDER: Provider = { id: "acme", name: "Acme", url: "https://acme.example", public_key: "" };
const INFO: AppInfo = { platform: "linux", demo: true, cli: "tag", version: "9.9.9", launchedAtLogin: false, legacyWantedTags: null };

describe("connecting in Settings", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("shows one Connect line, installs the signed add-on, and fills its places", async () => {
    const { pair, publicKey } = await keys();
    const provider = { ...PROVIDER, public_key: publicKey };
    const pkg = await signed(pair, await manifest({ buttons: [{ id: "report", place: "tag.actions", label: "Report" }] }));
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/connect")) return new Response(JSON.stringify({ device_code: "d", verification_url: "https://acme.example/a", interval: 1 }));
      if (url.endsWith("/connect/token")) return new Response(JSON.stringify({ status: "approved", token: "tok", account: { label: "Maya" } }));
      if (url.endsWith("/package")) return new Response(JSON.stringify(pkg));
      return new Response("{}");
    });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const synced = new Set<string>();
    const contributions: Contributions = {
      screens: { reports: { mount: (el) => { el.textContent = "Report screen"; } } },
      buttons: { report: { run: vi.fn() } },
      channel_switches: { sync: { get: async (t) => synced.has(t.channel.id), set: async (t, on) => { if (on) synced.add(t.channel.id); else synced.delete(t.channel.id); } } },
    };
    const loadModule = vi.fn().mockResolvedValue({ activate: () => contributions });
    const api = { open: vi.fn().mockResolvedValue(undefined), tag: vi.fn() } as unknown as Bridge;
    const openScreen = vi.fn();
    render(
      <AddonsProvider api={api} info={INFO} say={vi.fn()} openScreen={openScreen} tagIds={["maya"]} providers={[provider]} loadModule={loadModule}>
        <AddonConnections /><AddonNav /><AddonTagActions tag="maya" /><AddonChannelSwitches target={{ tag: "maya", channel: { id: "C1", name: "general" } }} />
      </AddonsProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Connect" }));
    expect(screen.getByText("Connect Acme")).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    await waitFor(() => expect(screen.getByText(/Connected as Maya · add-on 1.0.0/)).toBeTruthy());
    expect(api.open).toHaveBeenCalledWith("https://acme.example/a");
    expect(loadModule).toHaveBeenCalledWith(CODE);
    expect(store.get("acme")?.token).toBe("tok");
    fireEvent.click(screen.getByRole("button", { name: "Reports" }));
    expect(openScreen).toHaveBeenCalledWith("example/reports");
    fireEvent.click(screen.getByRole("button", { name: "Report" }));
    await waitFor(() => expect(contributions.buttons!.report.run).toHaveBeenCalledWith({ tag: "maya" }));
    const sync = await screen.findByRole("switch", { name: "Sync for #general" });
    expect(sync.getAttribute("aria-checked")).toBe("false");
    await act(async () => { fireEvent.click(sync); });
    await waitFor(() => expect(screen.getByRole("switch", { name: "Sync for #general" }).getAttribute("aria-checked")).toBe("true"));
    vi.useRealTimers();
  });

  it("never runs a stored package whose signature no longer checks out", async () => {
    const { pair } = await keys();
    const other = await keys();
    store.set("acme", { token: "tok", account: { label: "Maya" }, package: await signed(pair, await manifest()) });
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("offline"));
    const loadModule = vi.fn();
    render(
      <AddonsProvider api={{ open: vi.fn(), tag: vi.fn() } as unknown as Bridge} info={INFO} say={vi.fn()} openScreen={vi.fn()} tagIds={[]}
        providers={[{ ...PROVIDER, public_key: other.publicKey }]} loadModule={loadModule}>
        <AddonConnections />
      </AddonsProvider>,
    );
    await screen.findByText(/isn't signed by the provider/);
    expect(loadModule).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeTruthy();
  });
});
