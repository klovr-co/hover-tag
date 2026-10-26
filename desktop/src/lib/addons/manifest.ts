// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// The add-on manifest: what an add-on says it is and which fixed places it fills.
// See docs/reference/addon-interface.md.

/** Add-on interface versions this Tag.app can load. */
export const SUPPORTED_INTERFACES = [1] as const;

/** Places where a named button can go. */
export const BUTTON_PLACES = ["thread.actions", "tag.actions"] as const;
export type ButtonPlace = (typeof BUTTON_PLACES)[number];

export interface ScreenEntry { id: string; title: string; icon: string }
export interface KnowledgeEntry { id: string; title: string }
export interface ButtonEntry { id: string; place: ButtonPlace; label: string }
export interface SwitchEntry { id: string; label: string; detail?: string }

export interface Manifest {
  interface: number;
  id: string;
  name: string;
  version: string;
  /** The configured provider that signed it. */
  publisher: string;
  /** SHA-256 of the entry module, lowercase hex. */
  entry_sha256: string;
  /** `tag` commands the add-on may run, always with `--json`. */
  tag_commands: string[];
  screens: ScreenEntry[];
  knowledge_folders: KnowledgeEntry[];
  buttons: ButtonEntry[];
  channel_switches: SwitchEntry[];
}

export class ManifestError extends Error {
  /** `interface`: made for a different add-on interface version than this app has. */
  constructor(message: string, readonly kind: "invalid" | "interface" = "invalid") { super(message); }
}

const ID = /^[a-z0-9][a-z0-9-]{0,40}$/;
const KEYS = ["interface", "id", "name", "version", "publisher", "entry_sha256", "tag_commands",
  "screens", "knowledge_folders", "buttons", "channel_switches"];

const text = (value: unknown, field: string, max = 80): string => {
  if (typeof value !== "string" || !value.trim() || value.length > max) throw new ManifestError(`The add-on's ${field} is missing or too long.`);
  return value;
};

function entries<T>(value: unknown, field: string, read: (item: Record<string, unknown>) => T): T[] {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > 12) throw new ManifestError(`The add-on's ${field} list isn't valid.`);
  const seen = new Set<string>();
  return value.map((item) => {
    if (!item || typeof item !== "object") throw new ManifestError(`The add-on's ${field} list isn't valid.`);
    const record = item as Record<string, unknown>;
    if (typeof record.id !== "string" || !ID.test(record.id) || seen.has(record.id)) throw new ManifestError(`An entry in the add-on's ${field} has a bad id.`);
    seen.add(record.id);
    return read(record);
  });
}

/** Reads a signed manifest's text. Unknown fields are refused: an add-on fills only the places this version has. */
export function parseManifest(source: string, publisher: string): Manifest {
  let raw: unknown;
  try { raw = JSON.parse(source); } catch { throw new ManifestError("The add-on's manifest isn't valid JSON."); }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new ManifestError("The add-on's manifest isn't valid.");
  const m = raw as Record<string, unknown>;
  if (!Number.isInteger(m.interface)) throw new ManifestError("The add-on doesn't say which add-on interface it uses.");
  if (!(SUPPORTED_INTERFACES as readonly number[]).includes(m.interface as number)) {
    throw new ManifestError((m.interface as number) > Math.max(...SUPPORTED_INTERFACES)
      ? "This add-on needs a newer Tag.app. Update Tag, then connect again."
      : "This add-on was made for an older Tag.app and can't load.", "interface");
  }
  const unknown = Object.keys(m).filter((key) => !KEYS.includes(key));
  if (unknown.length) throw new ManifestError(`The add-on uses places this Tag.app doesn't have: ${unknown.join(", ")}.`);
  if (typeof m.id !== "string" || !ID.test(m.id)) throw new ManifestError("The add-on's id isn't valid.");
  if (m.publisher !== publisher) throw new ManifestError("The add-on names a different publisher than the one you connected.");
  if (typeof m.entry_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(m.entry_sha256)) throw new ManifestError("The add-on's code checksum is missing.");
  const commands = m.tag_commands ?? [];
  if (!Array.isArray(commands) || !commands.every((c) => typeof c === "string" && ID.test(c))) throw new ManifestError("The add-on's tag_commands list isn't valid.");
  return {
    interface: m.interface as number,
    id: m.id,
    name: text(m.name, "name"),
    version: text(m.version, "version", 40),
    publisher,
    entry_sha256: m.entry_sha256,
    tag_commands: commands as string[],
    screens: entries(m.screens, "screens", (e) => ({ id: e.id as string, title: text(e.title, "screen title", 40), icon: text(e.icon, "screen icon", 8) })),
    knowledge_folders: entries(m.knowledge_folders, "knowledge_folders", (e) => ({ id: e.id as string, title: text(e.title, "folder title", 40) })),
    buttons: entries(m.buttons, "buttons", (e) => {
      if (!(BUTTON_PLACES as readonly unknown[]).includes(e.place)) throw new ManifestError(`The add-on puts a button in an unknown place: ${String(e.place)}.`);
      return { id: e.id as string, place: e.place as ButtonPlace, label: text(e.label, "button label", 32) };
    }),
    channel_switches: entries(m.channel_switches, "channel_switches", (e) => ({
      id: e.id as string, label: text(e.label, "switch label", 40),
      ...(e.detail === undefined ? {} : { detail: text(e.detail, "switch detail", 160) }),
    })),
  };
}
