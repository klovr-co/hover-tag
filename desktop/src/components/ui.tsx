// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Small building blocks shared by every screen, in the Hover look.
import { useState, type MouseEvent, type ReactNode } from "react";
import { convertFileSrc } from "@tauri-apps/api/core";
import icon from "../../../assets/branding/tag-icon.png";
import { status, type Status, type TagRow } from "../lib/protocol";

export const tagIcon = icon;

/** 24-unit icons, drawn with the current text colour. */
const PATHS: Record<string, ReactNode> = {
  file: <><path d="M14 3H5v18h14V8zM14 3v5h5M8 13h8M8 17h6" /></>,
  image: <><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8" cy="8" r="1.5" /><path d="m3 17 5-5 4 4 4-6 5 7" /></>,
  gear: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></>,
  refresh: <><path d="M21 12a9 9 0 1 1-3-6.7L21 8" /><path d="M21 3v5h-5" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  warn: <><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /><path d="M12 9v4M12 17h.01" /></>,
  bang: <path d="M12 5v9M12 19h.01" />,
  up: <path d="M12 19V5M5 12l7-7 7 7" />,
  check: <path d="M20 6 9 17l-5-5" />,
  spark: <path d="M12 2l2.2 6.6L21 11l-6.8 2.4L12 20l-2.2-6.6L3 11l6.8-2.4z" />,
  play: <path d="M6 4l14 8-14 8z" />,
  stop: <rect x="5" y="5" width="14" height="14" rx="2" />,
  arrow: <path d="M5 12h14M13 6l6 6-6 6" />,
  left: <path d="M15 18l-6-6 6-6" />,
  right: <path d="M9 6l6 6-6 6" />,
  copy: <><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></>,
  paste: <><rect x="6" y="4" width="12" height="17" rx="2" /><path d="M9 4V3h6v1" /></>,
  external: <path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />,
  search: <><circle cx="11" cy="11" r="7" /><path d="M20 20l-4-4" /></>,
  user: <><circle cx="12" cy="8" r="4" /><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6" /></>,
  updown: <path d="M8 9l4-4 4 4M8 15l4 4 4-4" />,
  restart: <><path d="M21 12a9 9 0 1 1-3-6.7L21 8" /><path d="M21 3v5h-5" /></>,
  upload: <path d="M12 16V4M7 9l5-5 5 5M4 20h16" />,
  dice: <><rect x="3" y="3" width="18" height="18" rx="4" /><circle cx="8.5" cy="8.5" r="1.2" fill="currentColor" /><circle cx="15.5" cy="15.5" r="1.2" fill="currentColor" /><circle cx="15.5" cy="8.5" r="1.2" fill="currentColor" /><circle cx="8.5" cy="15.5" r="1.2" fill="currentColor" /><circle cx="12" cy="12" r="1.2" fill="currentColor" /></>,
  lock: <><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></>,
  disk: <path d="M12 3v12M7 10l5 5 5-5M4 21h16" />,
  wifi: <path d="M2 9a15 15 0 0 1 20 0M5 13a10 10 0 0 1 14 0M8.5 16.5a5 5 0 0 1 7 0M12 20h.01" />,
  star: <path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z" />,
  chat: <path d="M4 5h16v11H11l-5 4v-4H4z" />,
  share: <path d="M12 3v12M7 8l5-5 5 5M5 13v7h14v-7" />,
  chevdown: <path d="M6 9l6 6 6-6" />,
  close: <path d="M6 6l12 12M18 6 6 18" />,
};
const FILLED = new Set(["play", "stop", "spark"]);
const WEIGHT: Record<string, number> = { plus: 2.6, check: 3, bang: 3.4, up: 2.6, left: 2.6, right: 2.6, arrow: 2.4, updown: 2.4, chevdown: 3 };

export function Icon({ name, size = 14 }: { name: string; size?: number }) {
  const filled = FILLED.has(name);
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={filled ? "currentColor" : "none"} stroke={filled ? "none" : "currentColor"}
      strokeWidth={WEIGHT[name] ?? 2.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {PATHS[name]}
    </svg>
  );
}

type ButtonProps = {
  title: ReactNode; icon?: string; after?: string; onClick: () => void; disabled?: boolean; autoFocus?: boolean; small?: boolean; label?: string;
};

const button = (kind: string) => ({ title, icon, after, onClick, disabled, autoFocus, small, label }: ButtonProps) => (
  <button className={`p-btn ${kind}${small ? " sm" : ""}`} onClick={onClick} disabled={disabled} autoFocus={autoFocus} aria-label={label}>
    {icon && <Icon name={icon} />}{title}{after && <Icon name={after} />}
  </button>
);

/** The one navy button on a screen. */
export const Primary = button("ink");
export const Secondary = button("soft");
export const Quiet = button("quiet");

export const TextButton = ({ title, onClick, disabled }: { title: ReactNode; onClick: () => void; disabled?: boolean }) => (
  <button className="link" onClick={onClick} disabled={disabled}>{title}</button>
);

export const Back = ({ onClick }: { onClick: () => void }) => (
  <button className="back" onClick={onClick}><Icon name="left" />Back</button>
);

export const Spinner = ({ small, label = "Working" }: { small?: boolean; label?: string }) => (
  <span className={small ? "spin sm" : "spin"} role="progressbar" aria-label={label} />
);

export function Heading({ title, body }: { title: ReactNode; body?: ReactNode }) {
  return (
    <div>
      <div className="h2">{title}</div>
      {body && <p className="lead">{body}</p>}
    </div>
  );
}

// ---- The sky ------------------------------------------------------------------

const pix = (viewBox: string, rects: string) =>
  "data:image/svg+xml;utf8," + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="${viewBox}" shape-rendering="crispEdges">${rects}</svg>`);
const rows = (list: number[][], colour: string) =>
  list.map(([x, y, w]) => `<rect x="${x}" y="${y}" width="${w}" height="1" fill="${colour}"/>`).join("");
export const CLOUD = pix("0 0 24 8", rows([[9, 0, 5], [7, 1, 9], [17, 1, 3], [5, 2, 16], [3, 3, 19], [1, 4, 22], [0, 5, 24]], "#ffffff")
  + rows([[0, 6, 24], [2, 7, 20]], "#cfe8f9"));
export const MOON = pix("0 0 9 9", rows([[3, 0, 3], [1, 1, 7], [1, 2, 7], [0, 3, 9], [0, 4, 9], [0, 5, 9], [1, 6, 7], [1, 7, 7], [3, 8, 3]], "#f6e7a8")
  + [[5, 2], [6, 2], [2, 4], [5, 5], [6, 6]].map(([x, y]) => `<rect x="${x}" y="${y}" width="1" height="1" fill="#d9c47a"/>`).join(""));
const STARS = [[90, 14], [118, 34], [176, 10], [214, 30], [300, 16], [352, 36], [410, 10], [468, 24], [500, 40], [160, 44], [380, 22], [60, 46], [250, 52], [440, 58]];

type Cloud = [left: number, top: number, width: number, opacity?: number];
const CLOUDS: Record<string, Cloud[]> = {
  wide: [[250, 10, 96], [400, 16, 56, 0.85], [150, 24, 40, 0.55]],
  clear: [[250, 10, 96], [150, 24, 40, 0.55]],
  compact: [[330, 8, 64], [430, 30, 40, 0.7]],
  thin: [[300, 14, 56, 0.8]],
};

/** Pixel clouds by day, stars and a moon by night; both stand still with reduced motion. */
export function Decor({ height = 104, clouds = "wide", stars = height }: { height?: number; clouds?: keyof typeof CLOUDS; stars?: number }) {
  return (
    <>
      {CLOUDS[clouds].map(([left, top, width, opacity], i) => (
        <img key={`c${i}`} className="cloud" src={CLOUD} alt="" style={{ left, top, width, opacity }} />
      ))}
      {STARS.filter(([, y]) => y < stars - 30).map(([left, top], i) => (
        <span key={`s${i}`} className="star" style={{ left, top, animationDelay: `${(i % 5) * 0.6}s` }} />
      ))}
      <img className="moon" src={MOON} alt="" style={{ width: 18, left: 250, top: 14 }} />
    </>
  );
}

export type SkyKind = "home" | "compact" | "flow" | "tall" | "hero" | "ready" | "thin";
const SKY_HEIGHT: Record<SkyKind, number> = { home: 104, compact: 92, flow: 136, tall: 150, hero: 178, ready: 170, thin: 52 };

/** The sky header. It's also where the window is dragged from. */
export function Sky({ kind = "home", clouds, stars, children }: {
  kind?: SkyKind; clouds?: keyof typeof CLOUDS; stars?: number; children?: ReactNode;
}) {
  const height = SKY_HEIGHT[kind];
  return (
    <div className={`sky ${kind}`} onMouseDown={dragWindow}>
      <Decor height={height} clouds={clouds ?? (kind === "compact" ? "compact" : kind === "thin" ? "thin" : "wide")} stars={stars ?? height} />
      {children}
    </div>
  );
}

const CONTROLS = "button, a, input, textarea, select, label, [role=button], [role=switch]";

/**
 * Move the window from anywhere in a header, not just its bare background: Tauri's
 * drag region only answers presses on the marked element itself, and the header's
 * titles, avatars and clouds cover most of it.
 */
export function dragWindow(event: MouseEvent) {
  if (event.button !== 0 || !("__TAURI_INTERNALS__" in window) || (event.target as Element).closest(CONTROLS)) return;
  event.preventDefault();
  void import("@tauri-apps/api/window").then(({ getCurrentWindow }) => getCurrentWindow().startDragging())
    .catch(() => { /* the window just stays put */ });
}

/** A compact sky with a back button and a title, used by Settings and other inner screens. */
export function CompactSky({ title, sub, back, right }: { title: ReactNode; sub?: ReactNode; back?: () => void; right?: ReactNode }) {
  return (
    <Sky kind="compact" stars={60}>
      <div className="sky-row">
        {back && <button className="sky-btn" aria-label="Back" onClick={back}><Icon name="left" /></button>}
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2>{title}</h2>
          {sub && <div className="sum">{sub}</div>}
        </div>
        {right}
      </div>
    </Sky>
  );
}

// ---- Tags and workspaces -----------------------------------------------------------

const BADGE: Record<Status, string> = { online: "var(--green)", offline: "var(--faint)", setup: "var(--amber)", attention: "var(--red)" };

/** A local picture Tag saved, as something the window can show. */
export function source(path: string | null | undefined, revision?: string | null) {
  if (!path) return null;
  try {
    if (!("__TAURI_INTERNALS__" in window)) return null;
    const url = convertFileSrc(path);
    // Convert only the file path. Tauri ignores the query when opening the file,
    // while the webview uses it to reload an image overwritten at the same path.
    return revision ? `${url}?v=${encodeURIComponent(revision)}` : url;
  } catch {
    return null;
  }
}

/** Each Tag's Slack profile picture; otherwise the Tag waterdrop, tinted per Tag. */
export function Avatar({ row, size = 42, badge = true, className = "av" }: {
  row: TagRow | null; size?: number; badge?: boolean; className?: string;
}) {
  const [failedSource, setFailedSource] = useState<string | null>(null);
  const hue = row ? ([...row.id].reduce((sum, c) => sum + c.charCodeAt(0), 0) % 6) * 60 : 0;
  const avatarSource = source(row?.avatar);
  const picture = avatarSource !== failedSource ? avatarSource : null;
  const state = row ? status(row) : "offline";
  const img = (
    <img className={`${className}${state === "setup" ? " dim" : ""}`} alt="" width={size} height={size} src={picture ?? tagIcon}
      onError={() => setFailedSource(avatarSource)}
      style={{ width: size, height: size, borderRadius: size * 0.26, filter: picture ? undefined : `hue-rotate(${hue}deg)` }} />
  );
  if (!badge || !row) return img;
  return (
    <span className="avw">
      {img}
      <span className="badge" style={{ background: BADGE[state] }} aria-hidden="true" />
    </span>
  );
}

const WS_COLORS = ["#2297df", "#e0702b", "#4cb35a", "#8b5cf6", "#d9467a", "#14a39a"];
export const workspaceColor = (key: string) => WS_COLORS[(key.charCodeAt(0) + key.length) % WS_COLORS.length];

/** The Slack workspace's own icon, or its coloured first letter when Slack has none. */
export function WorkspaceMark({ label, icon: path, big }: { label: string; icon: string | null; big?: boolean }) {
  const [failedSource, setFailedSource] = useState<string | null>(null);
  const currentSource = source(path);
  const picture = currentSource !== failedSource ? currentSource : null;
  const cls = big ? "ws big" : "ws";
  return picture ? (
    <img className={cls} alt="" src={picture} onError={() => setFailedSource(currentSource)} />
  ) : (
    <span className={cls} style={{ background: label ? workspaceColor(label) : "var(--faint)" }} aria-hidden="true">
      {(label || "?")[0].toUpperCase()}
    </span>
  );
}

/** The setup owner's Slack picture, with a person symbol if unavailable. */
export function OwnerMark({ icon: path }: { icon?: string | null }) {
  const [failedSource, setFailedSource] = useState<string | null>(null);
  const currentSource = source(path);
  const picture = currentSource !== failedSource ? currentSource : null;
  return <span className="you">{picture
    ? <img src={picture} alt="" onError={() => setFailedSource(currentSource)} />
    : <Icon name="user" />}</span>;
}

export function Switch({ on, busy, onClick, label }: { on: boolean; busy: boolean; onClick: () => void; label: string }) {
  return (
    <button className={on ? "sw on" : "sw"} role="switch" aria-checked={on} aria-label={label}
      disabled={busy} onClick={(e) => { e.stopPropagation(); onClick(); }}>
      <i>{busy && <span className="spin" />}</i>
    </button>
  );
}

/** Grow a text box to show all of its text; pass as `ref` and call from `onChange`. */
export function fitText(el: HTMLTextAreaElement | null) {
  if (!el) return;
  el.style.height = "auto";
  el.style.height = `${el.scrollHeight + el.offsetHeight - el.clientHeight}px`;
}

export function ErrorLine({ children }: { children: ReactNode }) {
  return <div className="err-line selectable" role="alert"><Icon name="warn" />{children}</div>;
}

/** A short message at the bottom of the window. */
export function Toast({ text }: { text: string | null }) {
  return text ? <div className="toast" role="status">{text}</div> : null;
}
