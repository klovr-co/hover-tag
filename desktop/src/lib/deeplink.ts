// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
/** What a `hover-tag://` link opens: a Tag, or one Slack thread of a Tag. */
export interface DeepLink {
  tag: string;
  /** Set together for `hover-tag://tag/ID/thread/CHANNEL/THREAD_TS`. */
  channel?: string;
  thread?: string;
}

const TAG_ID = /^[A-Za-z0-9._-]+$/;
const CHANNEL = /^[CDG][A-Z0-9]{2,}$/;
const THREAD_TS = /^\d{6,12}\.\d{1,8}$/;

/** `hover-tag://tag/ID` or `hover-tag://tag/ID/thread/CHANNEL/THREAD_TS`, or null for anything else. */
export function parseDeepLink(link: string): DeepLink | null {
  let url: URL;
  try { url = new URL(link); } catch { return null; }
  if (url.protocol !== "hover-tag:" || url.hostname !== "tag") return null;
  let parts: string[];
  try { parts = url.pathname.replace(/^\/+|\/+$/g, "").split("/").map(decodeURIComponent); } catch { return null; }
  const [tag, kind, channel, thread] = parts;
  if (!tag || !TAG_ID.test(tag)) return null;
  if (parts.length === 1) return { tag };
  if (parts.length === 4 && kind === "thread" && CHANNEL.test(channel) && THREAD_TS.test(thread)) return { tag, channel, thread };
  return null;
}

/** The Tag a link opens, or null for anything else. */
export function deepLinkTarget(link: string): string | null {
  return parseDeepLink(link)?.tag ?? null;
}
