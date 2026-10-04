// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Light, dark or follow the Mac. Auto leaves `data-theme` off so the system decides.
import { useEffect, useState } from "react";

export type Appearance = "auto" | "light" | "dark";
export const APPEARANCES: Appearance[] = ["auto", "light", "dark"];
export const APPEARANCE_LABEL: Record<Appearance, string> = { auto: "Auto", light: "Light", dark: "Dark" };

const KEY = "tag.appearance";
const changed = "tag-appearance";

export function savedAppearance(): Appearance {
  try {
    const value = window.localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "auto";
  } catch {
    return "auto";
  }
}

export function applyAppearance(appearance: Appearance) {
  if (appearance === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = appearance;
}

export function setAppearance(appearance: Appearance) {
  try {
    if (appearance === "auto") window.localStorage.removeItem(KEY);
    else window.localStorage.setItem(KEY, appearance);
  } catch { /* the choice still applies until the app closes */ }
  applyAppearance(appearance);
  window.dispatchEvent(new Event(changed));
}

export function useAppearance(): Appearance {
  const [value, setValue] = useState(savedAppearance);
  useEffect(() => {
    const sync = () => setValue(savedAppearance());
    window.addEventListener(changed, sync);
    return () => window.removeEventListener(changed, sync);
  }, []);
  return value;
}

/** Whether the app is drawn at night now, for the few images CSS can't swap. */
export function useNight(): boolean {
  const appearance = useAppearance();
  const query = () => typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)");
  const [system, setSystem] = useState(() => { const q = query(); return q ? q.matches : false; });
  useEffect(() => {
    const q = query();
    if (!q) return;
    const sync = () => setSystem(q.matches);
    q.addEventListener("change", sync);
    return () => q.removeEventListener("change", sync);
  }, []);
  return appearance === "auto" ? system : appearance === "dark";
}
