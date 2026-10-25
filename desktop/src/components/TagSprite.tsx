// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// A Tag's waterdrop on a canvas, playing the animation for its mood. All the
// sprites on screen share one clock at the pixel art's 12 frames a second.
import { useEffect, useMemo, useRef, type CSSProperties } from "react";
import { reducedMotion } from "../lib/motion";
import { frame, PERIOD, prepare, RESTING, shift, TILE, type Mood, type Pixels, type Sprite } from "../lib/sprite";

const FPS = 12;
const WAKING_MS = 1800;
const drawers = new Set<(now: number) => void>();
let running = false, last = 0;
function tick(now: number) {
  if (!drawers.size) { running = false; return; }
  if (now - last >= 1000 / FPS - 1) { last = now; for (const draw of drawers) draw(now); }
  requestAnimationFrame(tick);
}
function onClock(draw: (now: number) => void) {
  drawers.add(draw);
  if (!running && typeof requestAnimationFrame === "function") { running = true; requestAnimationFrame(tick); }
  return () => { drawers.delete(draw); };
}

/**
 * A resting mood holds its still frame; going to sleep or waking up plays once on the way.
 * `still` shows the still frame, as reduced motion does.
 */
export function TagSprite({ sprite, mood, seed = 0, still = false, className, style }: {
  sprite: Sprite; mood: Mood; seed?: number; still?: boolean; className?: string; style?: CSSProperties;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  // Each refresh of the Tag list brings a new but equal sprite; that mustn't restart a transition.
  const look = JSON.stringify(sprite);
  const art = useMemo(() => prepare(JSON.parse(look) as Sprite), [look]);
  const shown = useRef<{ mood: Mood; at: number; from: Mood | null } | null>(null);
  useEffect(() => {
    const before = shown.current, at = performance.now();
    shown.current = { mood, at, from: before?.mood ?? null };
    // Starting keeps the eyes closed while the drop fills; ended sooner, the drop is still asleep.
    const from = before?.mood === "start" && before.from === "off" && at - before.at < WAKING_MS ? "off" : before?.mood ?? null;
    // Tests run without a canvas; there is nothing to draw on.
    const context = typeof ImageData === "function" ? canvas.current?.getContext("2d") : null;
    if (!context) return;
    const paint = (pixels: Pixels) => context.putImageData(new ImageData(pixels, TILE, TILE), 0, 0);
    const rest = () => paint(frame(art, mood, 0, { still: true }));
    if (still || reducedMotion()) { rest(); return; }
    const change = shift(from, mood);
    if (!change && RESTING.has(mood)) { rest(); return; }
    // Each mood starts from its beginning, so a moment such as Reply sent plays whole.
    const start = performance.now();
    let stop = () => {};
    const draw = (now: number) => {
      const t = (now - start) / 1000;
      if (change && t < PERIOD[change]) return paint(frame(art, change, t, { seed }));
      if (RESTING.has(mood)) { rest(); stop(); return; }
      paint(frame(art, mood, (t - (change ? PERIOD[change] : 0)) % PERIOD[mood], { seed }));
    };
    draw(start);
    stop = onClock(draw);
    return () => stop();
  }, [art, mood, seed, still]);
  // Decorative, like the picture it replaces: the row's text says the same state.
  return <canvas ref={canvas} className={className} width={TILE} height={TILE} style={style}
    aria-hidden="true" data-mood={mood} />;
}
