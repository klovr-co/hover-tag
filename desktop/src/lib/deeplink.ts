// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
/** The Tag a `hover-tag://tag/ID` link opens, or null for anything else. */
export function deepLinkTarget(link: string): string | null {
  let url: URL;
  try { url = new URL(link); } catch { return null; }
  if (url.protocol !== "hover-tag:" || url.hostname !== "tag") return null;
  const id = decodeURIComponent(url.pathname.replace(/^\/+|\/+$/g, ""));
  return /^[A-Za-z0-9._-]+$/.test(id) ? id : null;
}
