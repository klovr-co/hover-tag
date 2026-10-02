// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Miniature Slack windows that show each sign-in step. Always light, like Slack.
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";

const AUBERGINE = "#401440";
const SLACK_GREEN = "#007a5a";
const font: CSSProperties = { fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", color: "#1d1c1d" };

function SlackWindow({ children }: { children: ReactNode }) {
  return (
    <div style={{ ...font, display: "flex", width: 330, height: 124, borderRadius: 8, overflow: "hidden",
      boxShadow: "0 3px 8px rgba(0,0,0,0.12)", background: "#fff", colorScheme: "light" }}>
      <div style={{ width: 76, background: AUBERGINE, padding: 10, display: "flex", flexDirection: "column", gap: 7 }}>
        <div style={{ width: 22, height: 22, borderRadius: 5, background: "rgba(255,255,255,0.9)" }} />
        {[52, 44, 44].map((w, i) => (
          <div key={i} style={{ width: w, height: 5, borderRadius: 3, background: `rgba(255,255,255,${i ? 0.3 : 0.7})` }} />
        ))}
      </div>
      <div style={{ flex: 1, position: "relative" }}>{children}</div>
    </div>
  );
}

export function SlackComposer({ pasted }: { pasted: boolean }) {
  return (
    <SlackWindow>
      <div style={{ padding: 10, height: "100%", display: "flex", flexDirection: "column" }}>
        <div style={{ fontSize: 11, fontWeight: 700 }}># general</div>
        <div style={{ flex: 1 }} />
        <div style={{ display: "flex", alignItems: "center", padding: 7, border: "1px solid rgba(128,128,128,0.4)", borderRadius: 6 }}>
          <span style={{ fontSize: 11, color: pasted ? "#000" : "#888", fontFamily: pasted ? "ui-monospace, Menlo, monospace" : undefined }}>
            {pasted ? "/slackauthticket MjA1…" : "Message #general"}
          </span>
          <span style={{ flex: 1 }} />
          <span style={{ width: 19, height: 19, borderRadius: 4, background: pasted ? SLACK_GREEN : "rgba(128,128,128,0.4)",
            display: "grid", placeItems: "center" }}>
            <svg width="9" height="9" viewBox="0 0 16 16" fill="#fff" aria-hidden="true"><path d="M1 8 15 1 11 15 8 9z" /></svg>
          </span>
        </div>
      </div>
    </SlackWindow>
  );
}

function Modal({ children }: { children: ReactNode }) {
  return (
    <div style={{ position: "absolute", inset: 0, background: "rgba(0,0,0,0.35)", display: "grid", placeItems: "center" }}>
      <div style={{ width: 180, padding: 10, background: "#fff", borderRadius: 6, display: "flex", flexDirection: "column", gap: 5 }}>
        {children}
      </div>
    </div>
  );
}

const Line = ({ width }: { width: number }) => (
  <div style={{ width, height: 4, borderRadius: 2, background: "rgba(128,128,128,0.25)" }} />
);

export function SlackConfirmModal() {
  return (
    <SlackWindow>
      <Modal>
        <div style={{ fontSize: 10, fontWeight: 700 }}>Slack CLI Authentication</div>
        <Line width={135} /><Line width={105} /><Line width={120} />
        <div style={{ alignSelf: "flex-end", fontSize: 9, fontWeight: 700, color: "#fff", background: SLACK_GREEN,
          padding: "4px 9px", borderRadius: 4, outline: "2px solid var(--accent)", outlineOffset: 2 }}>Confirm</div>
      </Modal>
    </SlackWindow>
  );
}

export function SlackCodeModal() {
  return (
    <SlackWindow>
      <Modal>
        <div style={{ fontSize: 10, fontWeight: 700 }}>Submit Challenge Code</div>
        <Line width={140} />
        <div style={{ alignSelf: "flex-start", fontSize: 15, fontWeight: 700, fontFamily: "ui-monospace, Menlo, monospace",
          padding: "1px 5px", borderRadius: 4, background: "rgba(10,132,255,0.2)" }}>NwrFLzAy</div>
      </Modal>
    </SlackWindow>
  );
}

/** Paste and send, then Confirm, on a loop. Holds on Confirm when motion is reduced. */
export function SlackSequence({ frame: fixed }: { frame?: number }) {
  const reduce = typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (fixed !== undefined || reduce) return;
    const timer = setInterval(() => setTick((t) => t + 1), 1600);
    return () => clearInterval(timer);
  }, [fixed, reduce]);
  const frame = fixed ?? (reduce ? 2 : tick % 3);
  const layer = (n: number): CSSProperties => ({ position: "absolute", inset: 0, display: "grid", placeItems: "center",
    opacity: frame === n ? 1 : 0, transition: reduce ? undefined : "opacity 0.3s ease-in-out" });
  return (
    <div style={{ position: "relative", width: 330, height: 124 }} role="img"
      aria-label="Paste the line into a Slack message, send it, then click Confirm.">
      <div style={layer(0)}><SlackComposer pasted={false} /></div>
      <div style={layer(1)}><SlackComposer pasted /></div>
      <div style={layer(2)}><SlackConfirmModal /></div>
    </div>
  );
}
