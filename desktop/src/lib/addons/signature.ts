// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Checks an add-on package before anything in it runs: an Ed25519 signature
// over the manifest by the provider's configured key, then the code's checksum.
import { ManifestError, parseManifest, type Manifest } from "./manifest";

/** What a provider serves and Tag.app keeps: the manifest as signed text, the entry module, and the signature. */
export interface AddonPackage {
  format: "tag-addon/1";
  manifest: string;
  code: string;
  /** Base64 Ed25519 signature over SIGNED_PREFIX + manifest. */
  signature: string;
}

/** Signed bytes start with this, so a signature made for something else can't pass as a manifest's. */
export const SIGNED_PREFIX = "tag-addon-manifest/1\n";

const bytes = (base64: string) => Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
const hex = (buffer: ArrayBuffer) => [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, "0")).join("");

export async function sha256(text: string) {
  return hex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
}

/** The manifest of a package signed by `publicKey` (base64, 32 raw bytes) for provider `publisher`; throws otherwise. */
export async function verifyPackage(pkg: unknown, publicKey: string, publisher: string): Promise<{ manifest: Manifest; code: string }> {
  const p = pkg as Partial<AddonPackage> | null;
  if (!p || p.format !== "tag-addon/1" || typeof p.manifest !== "string" || typeof p.code !== "string" || typeof p.signature !== "string") {
    throw new ManifestError("The add-on download isn't a Tag add-on package.");
  }
  let key: CryptoKey;
  try {
    const raw = bytes(publicKey);
    if (raw.length !== 32) throw new Error("length");
    key = await crypto.subtle.importKey("raw", raw, { name: "Ed25519" }, false, ["verify"]);
  } catch {
    // Fail closed: without a usable key nothing loads.
    throw new ManifestError("This Tag.app can't check add-on signatures, so nothing was loaded.");
  }
  let valid = false;
  try { valid = await crypto.subtle.verify({ name: "Ed25519" }, key, bytes(p.signature), new TextEncoder().encode(SIGNED_PREFIX + p.manifest)); }
  catch { valid = false; }
  if (!valid) throw new ManifestError("The add-on isn't signed by the provider you connected, so nothing was loaded.");
  const manifest = parseManifest(p.manifest, publisher);
  if (await sha256(p.code) !== manifest.entry_sha256) throw new ManifestError("The add-on's code doesn't match its signed manifest, so nothing was loaded.");
  return { manifest, code: p.code };
}
