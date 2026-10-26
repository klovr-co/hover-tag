// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// `tag NAME export` and `tag import`: one Tag in one file, without passwords or tokens.
import exportExample from "../../../protocol/examples/tag-export.json";
import importExample from "../../../protocol/examples/tag-import.json";
import { parseJSON, title, type TagRow } from "./protocol";

export type Exported = typeof exportExample.export;
export type Imported = Omit<typeof importExample.imported, "name"> & { name: string | null };

export const exportArgs = (tag: string, path: string, withoutHistory = false) =>
  [tag, "export", `--output=${path}`, ...(withoutHistory ? ["--without-history"] : []), "--json"];
export const importArgs = (path: string) => ["import", path, "--json"];

/** "mayas-tag-2026-10-26.hovertag" */
export function exportFileName(row: TagRow, now = new Date()) {
  const name = title(row).toLowerCase().replace(/['’]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || row.id;
  return `${name}-${now.toISOString().slice(0, 10)}.hovertag`;
}

export function transferError(stdout: string, stderr: string): { text: string; code: string } {
  try {
    const parsed = parseJSON<{ error?: string; code?: string }>(stdout);
    if (parsed.error) return { text: parsed.error, code: parsed.code ?? "" };
  } catch { /* fall through to stderr */ }
  return { text: stderr.trim().split("\n").pop()?.replace(/^Error:\s*/, "") || "Tag couldn't move this Tag.", code: "" };
}

/** What `tag … export|import --json` answers in the demo app. */
export function demoTransfer(args: string[], rows: TagRow[]) {
  if (args[0] === "import") {
    return { schema_version: 1, ok: true, imported: { ...structuredClone(importExample.imported), tag: "mayas-tag-2" } };
  }
  const row = rows.find((r) => r.id === args[0]);
  const output = args.find((a) => a.startsWith("--output="))?.slice("--output=".length) ?? "";
  return { schema_version: 1, ok: true, tag: row?.id, export: { ...structuredClone(exportExample.export), path: output,
    history: !args.includes("--without-history") } };
}
