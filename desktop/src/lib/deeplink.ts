// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
/** What a `hover-tag://tag/ID` or `hover-tag://tag/ID/thread/CHANNEL/TS` link opens. */
export interface LinkTarget {
  tag: string;
  /** The Slack thread to open, or null for a plain Tag link. */
  channel: string | null;
  thread: string | null;
}

const TAG_ID = /^[A-Za-z0-9._-]+$/;
const CHANNEL = /^[CDG][A-Z0-9]{1,30}$/;
const THREAD_TS = /^\d{1,12}\.\d{1,8}$/;

/** The Tag, and the thread if any, a link opens; null for anything else. */
export function deepLinkTarget(link: string): LinkTarget | null {
  let url: URL;
  try { url = new URL(link); } catch { return null; }
  if (url.protocol !== "hover-tag:" || url.hostname !== "tag") return null;
  let parts: string[];
  try { parts = url.pathname.replace(/^\/+|\/+$/g, "").split("/").map(decodeURIComponent); } catch { return null; }
  const [tag, kind, channel, thread] = parts;
  if (!tag || !TAG_ID.test(tag)) return null;
  if (parts.length === 1) return { tag, channel: null, thread: null };
  if (parts.length === 4 && kind === "thread" && CHANNEL.test(channel) && THREAD_TS.test(thread)) return { tag, channel, thread };
  return null;
}

/** The thread a link opens, or null when it opens no thread. */
export function deepLinkThread(link: string): (LinkTarget & { channel: string; thread: string }) | null {
  const target = deepLinkTarget(link);
  return target?.channel && target.thread ? { ...target, channel: target.channel, thread: target.thread } : null;
}

/** The link that opens one of a Tag's threads in Tag.app. */
export const threadLink = (tag: string, channel: string, ts: string) =>
  `hover-tag://tag/${encodeURIComponent(tag)}/thread/${encodeURIComponent(channel)}/${encodeURIComponent(ts)}`;
