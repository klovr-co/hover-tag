// PROTOTYPE — throwaway. Floating variant switcher for UI prototypes; dev builds only.
import { useEffect } from "react";

export function usePrototypeVariant(keys: string[]): string {
  const value = new URLSearchParams(location.search).get("variant") ?? keys[0];
  return keys.includes(value) ? value : keys[0];
}

export function PrototypeSwitcher({ variants, current }: { variants: { key: string; name: string }[]; current: string }) {
  const index = Math.max(0, variants.findIndex((v) => v.key === current));
  const go = (step: number) => {
    const next = variants[(index + step + variants.length) % variants.length].key;
    const url = new URL(location.href);
    url.searchParams.set("variant", next);
    history.replaceState(null, "", url);
    dispatchEvent(new PopStateEvent("popstate"));
  };
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, [contenteditable]")) return;
      if (e.key === "ArrowLeft") go(-1);
      if (e.key === "ArrowRight") go(1);
    };
    addEventListener("keydown", key);
    return () => removeEventListener("keydown", key);
  });
  if (!import.meta.env.DEV) return null;
  const pill: React.CSSProperties = { position: "fixed", bottom: 14, left: "50%", transform: "translateX(-50%)", zIndex: 9999,
    display: "flex", alignItems: "center", gap: 10, padding: "6px 10px", borderRadius: 999, background: "#111", color: "#fff",
    font: "600 12px ui-monospace, Menlo, monospace", boxShadow: "0 6px 20px rgba(0,0,0,.35)" };
  const arrow: React.CSSProperties = { color: "#fff", padding: "2px 8px", fontSize: 14 };
  return (
    <div style={pill} aria-label="Prototype variant switcher">
      <button style={arrow} onClick={() => go(-1)}>←</button>
      <span>{variants[index].key} ({variants[index].name})</span>
      <button style={arrow} onClick={() => go(1)}>→</button>
    </div>
  );
}
