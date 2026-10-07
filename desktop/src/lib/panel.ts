// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// A side panel beside a screen widens the window instead of squeezing the page.
import { createContext, useContext, useEffect } from "react";

/** Window widths: everyday screens, and Tag detail's Slack layout. */
export const WIDTH = 520;
export const WIDE = 800;

/** The conversation panel's width, added to the window while it is open. */
export const PANEL_WIDTH = 440;

/** Ask the window for `extra` pixels on the right; 0 gives them back. */
export const WindowPanel = createContext<(extra: number) => void>(() => {});

/** Whether the screen has room to widen the window by `extra` beside `width`. */
export function fitsBeside(width: number, extra: number, screen: { availWidth: number } = window.screen) {
  return screen.availWidth >= width + extra;
}

/** Widen the window while `open`, and shrink it back when closed or unmounted. */
export function useWindowPanel(open: boolean, extra = PANEL_WIDTH) {
  const widen = useContext(WindowPanel);
  useEffect(() => {
    widen(open ? extra : 0);
    return () => widen(0);
  }, [widen, open, extra]);
}
