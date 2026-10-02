// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Small building blocks shared by every screen.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { convertFileSrc } from "@tauri-apps/api/core";
import icon from "../../../../assets/branding/tag-icon.png";
import { status, type TagRow } from "../lib/protocol";

export const tagIcon = icon;

const PATHS: Record<string, string> = {
  plus: "M8 3v10M3 8h10",
  refresh: "M13 8a5 5 0 1 1-1.5-3.6M13 2.5V5h-2.5",
  play: "M5 3.5v9l7-4.5z",
  stop: "M4.5 4.5h7v7h-7z",
  more: "M3.5 8h.01M8 8h.01M12.5 8h.01",
  right: "M6 3.5 10.5 8 6 12.5",
  left: "M10 3.5 5.5 8l4.5 4.5",
  copy: "M5.5 5.5h7v7h-7zM3.5 10.5v-7h7",
  paste: "M5.5 3h5v2h-5zM4 4H3v9.5h10V4h-1",
  external: "M6.5 3.5h-3v9h9v-3M9 3h4v4M13 3 7.5 8.5",
  check: "M3.5 8.5 6.5 11.5 12.5 4.5",
  warning: "M8 2.5 14 13H2zM8 6.5v3M8 11.2v.01",
  star: "M8 2.2l1.8 3.7 4 .6-2.9 2.8.7 4L8 11.4l-3.6 1.9.7-4L2.2 6.5l4-.6z",
  chat: "M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z",
  share: "M8 2.5v8M5 5.5l3-3 3 3M3.5 8.5v5h9v-5",
  gear: "M6.6 1.8h2.8l.4 1.7 1.2.7 1.7-.5 1.4 2.4-1.3 1.2v1.4l1.3 1.2-1.4 2.4-1.7-.5-1.2.7-.4 1.7H6.6l-.4-1.7-1.2-.7-1.7.5-1.4-2.4 1.3-1.2V7.3L1.9 6.1l1.4-2.4 1.7.5 1.2-.7zM8 6.2a1.8 1.8 0 1 0 0 3.6 1.8 1.8 0 0 0 0-3.6z",
  logs: "M3.5 2.5h9v11h-9zM5.5 5.5h5M5.5 8h5M5.5 10.5h3",
  download: "M8 2.5v8M5 7.5l3 3 3-3M3 13.5h10",
  wifi: "M2 6.5a9 9 0 0 1 12 0M4 9a6 6 0 0 1 8 0M6 11.5a3 3 0 0 1 4 0M8 13.5h.01",
};

export function Icon({ name, size = 14 }: { name: keyof typeof PATHS | string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill={name === "play" || name === "stop" ? "currentColor" : "none"}
      stroke="currentColor" strokeWidth={name === "more" ? 2.6 : 1.5} strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true">
      <path d={PATHS[name]} />
    </svg>
  );
}

type ButtonProps = { title: string; icon?: string; onClick: () => void; disabled?: boolean; autoFocus?: boolean };

export const Primary = ({ title, icon, onClick, disabled, autoFocus }: ButtonProps) => (
  <button className="btn primary" onClick={onClick} disabled={disabled} autoFocus={autoFocus}>
    {icon && <Icon name={icon} />}{title}
  </button>
);

export const Secondary = ({ title, icon, onClick, disabled }: ButtonProps) => (
  <button className="btn" onClick={onClick} disabled={disabled}>{icon && <Icon name={icon} />}{title}</button>
);

export const TextButton = ({ title, onClick }: { title: string; onClick: () => void }) => (
  <button className="text-btn" onClick={onClick}>{title}</button>
);

export const Back = ({ onClick }: { onClick: () => void }) => (
  <button className="text-btn" onClick={onClick}><Icon name="left" size={12} />Back</button>
);

export const Spinner = ({ small }: { small?: boolean }) => (
  <span className={small ? "spinner small" : "spinner"} role="progressbar" aria-label="Working" />
);

export function Header({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="header">
      <img src={tagIcon} alt="" />
      <div className="stack gap-4">
        <div className="title2">{title}</div>
        <div className="secondary" style={{ fontSize: 14 }}>{subtitle}</div>
      </div>
    </div>
  );
}

export function Heading({ title, body }: { title: string; body?: string }) {
  return (
    <div className="stack gap-4">
      <div className="title3">{title}</div>
      {body && <div className="secondary">{body}</div>}
    </div>
  );
}

/** Each Tag's Slack profile picture; otherwise the Tag waterdrop, tinted per Tag. */
export function Avatar({ row, size = 38 }: { row: TagRow | null; size?: number }) {
  const [failed, setFailed] = useState(false);
  const hue = row ? ([...row.id].reduce((sum, c) => sum + c.charCodeAt(0), 0) % 6) * 60 : 0;
  const picture = row?.avatar && !failed ? source(row.avatar) : null;
  return (
    <img className="avatar" alt="" width={size} height={size} src={picture ?? tagIcon}
      onError={() => setFailed(true)}
      style={{
        borderRadius: size * 0.26,
        filter: `${picture ? "" : `hue-rotate(${hue}deg)`} ${row && status(row) === "setup" ? "saturate(0.2)" : ""}`,
      }} />
  );
}

function source(path: string) {
  try {
    return "__TAURI_INTERNALS__" in window ? convertFileSrc(path) : null;
  } catch {
    return null;
  }
}

export function Switch({ on, busy, onClick, label }: { on: boolean; busy: boolean; onClick: () => void; label: string }) {
  return (
    <button className={on ? "switch on" : "switch"} role="switch" aria-checked={on} aria-label={label}
      title={on ? "Stop this Tag" : "Start this Tag"} disabled={busy} onClick={onClick}>
      <span className="knob">{busy && <Spinner small />}</span>
    </button>
  );
}

/** A small menu under a "…" button; closes on outside click or Escape. */
export function MoreMenu({ label, items }: { label: string; items: [string, () => void][] }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", close);
    return () => { window.removeEventListener("mousedown", close); window.removeEventListener("keydown", close); };
  }, [open]);
  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button className="icon-btn" style={{ background: "none" }} aria-label={label} aria-haspopup="menu"
        aria-expanded={open} onClick={() => setOpen(!open)}>
        <Icon name="more" />
      </button>
      {open && (
        <div className="menu" role="menu">
          {items.map(([title, action]) => (
            <button key={title} role="menuitem" onClick={() => { setOpen(false); action(); }}>{title}</button>
          ))}
        </div>
      )}
    </div>
  );
}

export function ErrorLine({ children }: { children: ReactNode }) {
  return <div className="row gap-6 error callout" role="alert"><Icon name="warning" />{children}</div>;
}
