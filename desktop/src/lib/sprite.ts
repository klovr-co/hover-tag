// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// The Tag waterdrop, drawn and animated in code from the same 28 × 34 map and
// look as its Slack picture (scripts/tag_mascot.json and `picture.sprite` from
// `tag list --json`). Ported from prototypes/tag-animation: every frame
// re-samples the map, so the drop can blink, hop and squash and still look
// like the art. Pure functions; TagSprite puts the pixels on a canvas.
import mascot from "../../../scripts/tag_mascot.json";

/** One look, as `tag list --json` reports it in `picture.sprite`. */
export interface Sprite {
  element: string;
  background: string;
  /** A colour for every letter of the map; B–G are the background. */
  palette: Record<string, string>;
  /** The look's decoration: column, row, cells wide, cells high, colour. */
  marks: [number, number, number, number, string][];
}

export type Mood = "idle" | "start" | "setup" | "work" | "reply" | "mention" | "attn" | "off";
/** A mood, or a change between two moods that plays once. */
export type Scene = Mood | "sleep" | "wake";

/** How long one pass of each animation takes, in seconds. */
export const PERIOD: Record<Scene, number> = {
  idle: 6.6, start: 4.6, setup: 3, work: 3.2, reply: 3.2, mention: 3, attn: 2.4, off: 4, sleep: 2.6, wake: 1.8,
};

/** Moods that hold still: a Tag only moves when something is happening. */
export const RESTING: ReadonlySet<Mood> = new Set<Mood>(["idle", "off"]);

/** The change to play once when a Tag goes from one mood to another, or null. */
export function shift(before: Mood | null, after: Mood): "sleep" | "wake" | null {
  if (!before || before === after) return null;
  if (after === "off") return "sleep";
  // Switching on already plays Starting, which wakes the drop by itself.
  if (before === "off" && after === "idle") return "wake";
  return null;
}

// ---- the map ------------------------------------------------------------------
const PIXELS: string[] = mascot.pixels;
const W = PIXELS[0].length, H = PIXELS.length;

/** The canvas matches the Slack picture: 512 px at 12 px per cell is about 43 cells, the drop centred. */
export const TILE = 43;
/** Where the map sits on the canvas, as in the Slack picture. */
export const LEFT = Math.floor((TILE - W) / 2), TOP = Math.floor((TILE - H) / 2);

const key = (x: number, y: number) => y * W + x;
// The background floods in from the edges; pale glints inside the drop stay part of it.
const OUTSIDE = new Set<number>();
{
  const pending: [number, number][] = [];
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) if (!x || !y || x === W - 1 || y === H - 1) pending.push([x, y]);
  while (pending.length) {
    const [x, y] = pending.pop()!;
    if (x < 0 || y < 0 || x >= W || y >= H || OUTSIDE.has(key(x, y)) || !"BCDEFG".includes(PIXELS[y][x])) continue;
    OUTSIDE.add(key(x, y));
    pending.push([x - 1, y], [x + 1, y], [x, y - 1], [x, y + 1]);
  }
}
const inShape = (x: number, y: number) => x >= 0 && y >= 0 && x < W && y < H && !OUTSIDE.has(key(x, y));

// The largest connected piece is the body; the rest is the little droplet above the curl.
const PIECE = new Map<number, number>();
const pieceSizes: number[] = [];
for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
  if (!inShape(x, y) || PIECE.has(key(x, y))) continue;
  const id = pieceSizes.length, stack: [number, number][] = [[x, y]];
  let size = 0;
  while (stack.length) {
    const [i, j] = stack.pop()!;
    if (!inShape(i, j) || PIECE.has(key(i, j))) continue;
    PIECE.set(key(i, j), id); size++;
    stack.push([i - 1, j], [i + 1, j], [i, j - 1], [i, j + 1]);
  }
  pieceSizes.push(size);
}
const BODY_PIECE = pieceSizes.indexOf(Math.max(...pieceSizes));
const inBody = (x: number, y: number) => inShape(x, y) && PIECE.get(key(x, y)) === BODY_PIECE;
const inDrop = (x: number, y: number) => inShape(x, y) && PIECE.get(key(x, y)) !== BODY_PIECE;
const isEdge = (x: number, y: number) => inShape(x, y) && (!inShape(x - 1, y) || !inShape(x + 1, y) || !inShape(x, y - 1) || !inShape(x, y + 1));

const bodyCells: [number, number][] = [], dropCells: [number, number][] = [];
for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
  if (inBody(x, y)) bodyCells.push([x, y]);
  else if (inDrop(x, y)) dropCells.push([x, y]);
}
const xs = bodyCells.map(([x]) => x), ys = bodyCells.map(([, y]) => y);
/** Squash and stretch pivot on the bottom centre of the body. */
const CX = (Math.min(...xs) + Math.max(...xs) + 1) / 2, BOTTOM = Math.max(...ys) + 1;
const BODY_H = BOTTOM - Math.min(...ys);
const centre = (cells: [number, number][]) => [
  cells.reduce((s, [x]) => s + x, 0) / cells.length + .5, cells.reduce((s, [, y]) => s + y, 0) / cells.length + .5];
const [DCX, DCY] = centre(dropCells);
const [BCX, BCY] = centre(bodyCells);

// ---- the face -------------------------------------------------------------------
// Each eye is a 2 × 4 block with a glint; the smile below them never changes.
const EYE_COLUMNS = [[8, 9], [17, 18]], EYE_ROWS = [19, 20, 21, 22];
const DARK: [number, number] = [8, 21];
const SKIN: [number, number][] = [[10, 20], [16, 20]];
const EYES = new Set<string>();
for (const columns of EYE_COLUMNS) for (const x of columns) for (const y of EYE_ROWS) EYES.add(`${x},${y}`);
type Eyes = "open" | "wide" | "closed" | "sleepy" | "happy";
/** Eye shapes: map cell → the cell whose colour it takes. */
const EYEP: Record<Eyes, Map<string, [number, number]>> = {
  open: new Map(), wide: new Map(), closed: new Map(), sleepy: new Map(), happy: new Map(),
};
for (const columns of EYE_COLUMNS) for (const x of columns) {
  for (const y of EYE_ROWS) { EYEP.open.set(`${x},${y}`, [x, y]); EYEP.wide.set(`${x},${y}`, [x, y]); }
  EYEP.wide.set(`${x},18`, DARK);
  EYEP.closed.set(`${x},22`, DARK);
  EYEP.sleepy.set(`${x},21`, DARK); EYEP.sleepy.set(`${x},22`, DARK);
}
for (const [x, y] of [[7, 21], [8, 20], [9, 20], [10, 21], [16, 21], [17, 20], [18, 20], [19, 21]] as const) EYEP.happy.set(`${x},${y}`, DARK);

/** The outline in order around the body, for the setup chaser. */
const RING = bodyCells.filter(([x, y]) => isEdge(x, y))
  .sort((a, b) => Math.atan2(a[1] + .5 - BCY, a[0] + .5 - BCX) - Math.atan2(b[1] + .5 - BCY, b[0] + .5 - BCX));

// ---- one look, ready to sample -----------------------------------------------------
type RGB = [number, number, number];
const rgb = (hex: string): RGB => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as RGB;
const mix = (a: RGB, b: RGB, t: number): RGB => a.map((v, i) => v + (b[i] - v) * t) as RGB;

export interface Art {
  background: RGB;
  /** Colour of every map cell, with the look's marks painted over the drop. */
  cell: (x: number, y: number) => RGB;
  /** Marks that sit beside the drop, such as bubbles and sparkles; they float with it. */
  beside: { x: number; y: number; c: RGB }[];
  tone: { light: RGB; mid: RGB; dark: RGB };
}

export function prepare(sprite: Sprite): Art {
  const colours = new Map<string, RGB>(Object.entries(sprite.palette).map(([k, v]) => [k, rgb(v)]));
  const fallback = rgb(sprite.palette.N ?? sprite.background);
  const cells: RGB[] = PIXELS.flatMap((row) => [...row].map((k) => colours.get(k) ?? fallback));
  const beside = new Map<string, { x: number; y: number; c: RGB }>();
  for (const [column, row, wide, high, colour] of sprite.marks) {
    for (let y = row; y < row + high; y++) for (let x = column; x < column + wide; x++) {
      if (inShape(x, y)) cells[key(x, y)] = rgb(colour);
      else beside.set(`${x},${y}`, { x, y, c: rgb(colour) });
    }
  }
  return {
    background: rgb(sprite.background),
    cell: (x, y) => cells[key(x, y)],
    beside: [...beside.values()],
    tone: { light: colours.get("I") ?? fallback, mid: colours.get("N") ?? fallback, dark: colours.get("S") ?? fallback },
  };
}

// ---- one Tag, procedurally transformed ------------------------------------------------
interface Pose {
  x: number; y: number; sx?: number; sy?: number; eyes?: Eyes; look?: [number, number];
  lean?: number; wob?: number; wobPh?: number; shine?: number | null; tint?: number; grey?: number;
  alpha?: number; ghost?: number | null; fill?: number | null; fillPh?: number;
  drop?: { dx?: number; dy?: number; s?: number } | false;
}
interface Part { x: number; y: number; c: RGB; a?: number; under?: boolean }

function effects(t: Pose, c: RGB, i: number, j: number, isEye: boolean): [number, number, number, number] {
  let a = t.alpha ?? 1;
  const edge = isEdge(i, j);
  if (t.shine != null && !isEye && !edge) { const d = Math.abs(i - j * .6 - t.shine); if (d < 2) c = mix(c, [255, 255, 255], .5 * (1 - d / 2)); }
  if (t.tint) c = mix(c, [255, 64, 64], t.tint * (edge ? 1 : .3));
  if (t.grey) { const l = .3 * c[0] + .59 * c[1] + .11 * c[2]; c = mix(c, [l * 1.02, l * 1.02, l * 1.08], t.grey); }
  if (t.ghost != null && !edge) { c = mix(c, [255, 255, 255], .55); a *= t.ghost * 1.4; }
  if (t.fill != null) {
    const line = BOTTOM - t.fill * BODY_H * 1.03 + Math.sin(i * .7 + (t.fillPh ?? 0)) * .9;
    if (j < line) { c = mix(c, [255, 255, 255], .55); a *= edge ? .6 : .25; }
  }
  return [c[0], c[1], c[2], a];
}

function sampleBody(art: Art, t: Pose, x: number, y: number) {
  const sx = t.sx ?? 1, sy = t.sy ?? 1;
  let lx = x + .5 - t.x;
  const ly = y + .5 - t.y;
  const lift = Math.max(0, (BOTTOM - ly) / BODY_H);
  if (t.wob) lx -= t.wob * Math.sin(ly * .45 - (t.wobPh ?? 0)) * lift;
  if (t.lean) lx -= t.lean * lift;
  const i = Math.floor((lx - CX) / sx + CX), j = Math.floor((ly - BOTTOM) / sy + BOTTOM);
  if (!inBody(i, j)) return null;
  const lk = t.look ?? [0, 0], pattern = EYEP[t.eyes ?? "open"].get(`${i - lk[0]},${j - lk[1]}`);
  const src = pattern ?? (EYES.has(`${i},${j}`) ? SKIN[i < CX ? 0 : 1] : [i, j]);
  return effects(t, art.cell(src[0], src[1]), i, j, !!pattern);
}

function sampleDrop(art: Art, t: Pose, x: number, y: number) {
  if (t.drop === false) return null;
  const d = t.drop ?? {}, sx = t.sx ?? 1, sy = t.sy ?? 1, s = d.s ?? 1;
  if (s <= 0) return null;
  const cx = t.x + (DCX - CX) * sx + CX + (d.dx ?? 0), cy = t.y + (DCY - BOTTOM) * sy + BOTTOM + (d.dy ?? 0);
  const i = Math.floor((x + .5 - cx) / s + DCX), j = Math.floor((y + .5 - cy) / s + DCY);
  if (!inDrop(i, j)) return null;
  return effects({ ...t, shine: null, fill: null, ghost: null }, art.cell(i, j), i, j, false);
}

/** RGBA, TILE × TILE, ready for ImageData. */
export type Pixels = Uint8ClampedArray<ArrayBuffer>;

/** RGBA pixels for one frame on a TILE × TILE canvas, over the look's own background. */
function draw(art: Art, tag: Pose, parts: Part[] = []): Pixels {
  const px = new Uint8ClampedArray(TILE * TILE * 4);
  const put = (x: number, y: number, c: readonly number[], a = 1) => {
    x = Math.round(x); y = Math.round(y);
    if (x < 0 || y < 0 || x >= TILE || y >= TILE || a <= 0) return;
    const k = (y * TILE + x) * 4;
    for (let n = 0; n < 3; n++) px[k + n] = c[n] * a + px[k + n] * (1 - a);
    px[k + 3] = 255;
  };
  for (let y = 0; y < TILE; y++) for (let x = 0; x < TILE; x++) put(x, y, art.background);
  for (const p of parts) if (p.under) put(p.x, p.y, p.c, p.a ?? 1);
  for (let y = 0; y < TILE; y++) for (let x = 0; x < TILE; x++) {
    const c = sampleBody(art, tag, x, y) ?? sampleDrop(art, tag, x, y);
    if (c) put(x, y, c, c[3]);
  }
  for (const b of art.beside) {
    const [r, g, bl, a] = effects({ ...tag, shine: null, fill: null, ghost: null }, b.c, -1, -1, false);
    put(tag.x + b.x, tag.y + b.y, [r, g, bl], a);
  }
  for (const p of parts) if (!p.under) put(p.x, p.y, p.c, p.a ?? 1);
  return px;
}

// ---- motion helpers ------------------------------------------------------------------
const clamp = (v: number, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const ease = (u: number) => (u < .5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2);
const back = (u: number) => 1 + 2.70158 * (u - 1) ** 3 + 1.70158 * (u - 1) ** 2;
// Blink at the end of each 3.3 s, so an animation's first frame has open eyes.
const blink = (t: number, off = 0): Eyes => (((t + off) % 3.3) > 3.15 ? "closed" : "open");
/** Squash before, stretch in the air, a jelly landing. */
function hop(t: number, t0: number, height: number, dur: number) {
  if (t < t0 - .18 || t > t0 + dur + .6) return { dy: 0, sx: 1, sy: 1 };
  if (t < t0) { const u = (t - t0 + .18) / .18; return { dy: 0, sx: 1 + .12 * u, sy: 1 - .12 * u }; }
  if (t < t0 + dur) { const u = (t - t0) / dur, k = Math.abs(Math.cos(Math.PI * u)); return { dy: -Math.round(Math.sin(Math.PI * u) * height), sx: 1 - .06 * k, sy: 1 + .1 * k }; }
  const u = t - t0 - dur, a = .14 * Math.exp(-6 * u) * Math.cos(16 * u);
  return { dy: 0, sx: 1 + a, sy: 1 - a };
}
const glyph = (parts: Part[], rows: string[], x: number, y: number, c: RGB, a = 1) =>
  rows.forEach((r, j) => [...r].forEach((ch, i) => { if (ch !== ".") parts.push({ x: x + i, y: y + j, c, a }); }));
const Z = ["####", "..#.", ".#..", "####"], BANG = ["##", "##", "##", "..", "##"], AT = [".###.", "#..##", "#.#.#", "#..##", ".##.."];
const RED: RGB = [229, 72, 77], SLACK_RED: RGB = [224, 30, 90], GREY: RGB = [154, 160, 170], AMBER: RGB = [245, 165, 36], GREEN: RGB = [47, 191, 113];

// ---- the animations ------------------------------------------------------------------
// t runs from 0 to PERIOD[mood]; `seed` keeps several Tags from blinking together.
function pose(art: Art, mood: Scene, t: number, seed: number): { tag: Pose; parts: Part[] } {
  const x = LEFT, y = TOP, parts: Part[] = [];
  switch (mood) {
    case "idle":
      return { parts, tag: { x, y: y + Math.round(Math.sin(t * 2.1 + seed) * .8), eyes: blink(t, seed),
        drop: { dy: Math.round(Math.sin(t * 2.1 - 1.4 + seed) * 1.2) } } };
    case "start": {
      const h = hop(t, 2.0, 5, .5), fill = t < 1.8 ? ease(t / 1.8) : null;
      if (t > 2.5 && t < 3.6) {
        const u = (t - 2.5) / 1.1, rx = 5 + u * 14, ry = rx * .22;
        for (let a = 0; a < Math.PI * 2; a += .05) parts.push({ x: x + CX + Math.cos(a) * rx, y: y + BOTTOM + 1 + Math.sin(a) * ry, c: GREEN, a: (1 - u) * .9, under: true });
      }
      return { parts, tag: { x, y: y + h.dy, sx: h.sx, sy: h.sy, fill, fillPh: t * 7, eyes: t < 1.8 ? "closed" : blink(t) } };
    }
    case "setup": {
      const n = RING.length, head = Math.floor((t / 1.5) * n) % n;
      for (let k = 0; k < 6; k++) { const [i, j] = RING[(head - k + n) % n]; parts.push({ x: x + i, y: y + j, c: AMBER, a: 1 - k / 6 }); }
      return { parts, tag: { x, y, ghost: .28 + .14 * Math.sin((t * Math.PI * 4) / 3), alpha: .9, eyes: blink(t, seed) } };
    }
    case "work": {
      const u = (t % 1.6) / 1.6;
      return { parts, tag: { x, y, shine: -12 + u * u * 40, look: [0, -1], eyes: blink(t, 1),
        drop: { dx: Math.round(Math.cos(t * 3) * 1.6), dy: Math.round(Math.sin(t * 3) * 1.2) } } };
    }
    case "reply": {
      let drop: Pose["drop"] = {}, sy = 1, sx = 1, eyes: Eyes = blink(t);
      if (t < .4) { const u = t / .4; drop = { dy: Math.round(u) }; sy = 1 - .05 * u; sx = 1 + .03 * u; }
      else if (t < 1.2) {
        const u = (t - .4) / .8, p = (v: number) => ({ dx: v * 20, dy: -10 * v + 6 * v * v });
        drop = p(u); eyes = "happy";
        const k = .13 * Math.exp(-6 * u) * Math.cos(14 * u); sy = 1 + k; sx = 1 - k * .6;
        for (const [b, a] of [[.06, .5], [.12, .25]]) if (u > b) { const q = p(u - b); parts.push({ x: x + DCX + q.dx, y: y + DCY + q.dy, c: art.tone.light, a }); }
      } else if (t < 1.7) { drop = { s: 0 }; eyes = "happy"; }
      else if (t < 2.3) drop = { s: back((t - 1.7) / .6) };
      return { parts, tag: { x, y, sx, sy, eyes, drop } };
    }
    case "mention": {
      let sy = 1, sx = 1, eyes: Eyes = blink(t), ddy = 0;
      if (t > .15 && t < 1.2) { const u = t - .15, k = .12 * Math.exp(-5 * u) * Math.cos(15 * u); sy = 1 + k; sx = 1 - k * .5; eyes = "wide"; ddy = -Math.round(3 * Math.exp(-4 * u)); }
      if (t > .15 && t < 1.8) { const u = (t - .15) / 1.65; glyph(parts, AT, 1, 11 - Math.round(u * 4), SLACK_RED, u < .7 ? 1 : (1 - u) / .3); }
      return { parts, tag: { x, y, sx, sy, eyes, drop: { dy: ddy } } };
    }
    case "attn": {
      const u = clamp(t / .5), shake = t < .5 ? Math.round(Math.sin(t * 55) * 1.2 * (1 - u)) : 0;
      glyph(parts, BANG, 3, 4, RED, .6 + .4 * Math.sin(t * 5));
      return { parts, tag: { x: x + shake, y, tint: t < .5 ? .8 * (1 - u) + .3 : .3 + .15 * Math.sin(t * 5), eyes: t < .5 ? "wide" : blink(t, seed) } };
    }
    case "off":
      for (const off of [0, 2]) { const u = ((t + off) % 4) / 4; glyph(parts, Z, 35 + Math.round(u * 3), 13 - Math.round(u * 10), GREY, Math.sin(Math.PI * u)); }
      return { parts, tag: { x, y: y + 1, grey: .85, alpha: .85, eyes: "closed", drop: { dy: 1 } } };
    case "sleep": {
      // Awake to off: the eyelids droop, the drop nods off as its colour drains, and one Z drifts up.
      const drain = ease(clamp((t - .5) / 1)), nod = t > .7 && t < 1.5 ? Math.round(Math.sin(((t - .7) / .8) * Math.PI)) : 0;
      if (t > 1.1) { const u = clamp((t - 1.1) / 1.5); glyph(parts, Z, 35 + Math.round(u * 3), 13 - Math.round(u * 10), GREY, Math.sin(Math.PI * u)); }
      return { parts, tag: { x, y: y + nod, sy: 1 - .04 * nod, grey: .85 * drain, alpha: 1 - .15 * drain,
        eyes: t < .35 ? "open" : t < .8 ? "sleepy" : "closed", drop: { dy: nod } } };
    }
    case "wake": {
      // Off to awake: the colour comes back, the eyes open wide, and a small stretch.
      const drain = 1 - ease(clamp(t / .6)), h = hop(t, .8, 2, .35);
      return { parts, tag: { x, y: y + h.dy, sx: h.sx, sy: h.sy, grey: .85 * drain, alpha: 1 - .15 * drain,
        eyes: t < .4 ? "closed" : t < .65 ? "sleepy" : t < 1.3 ? "wide" : "open" } };
    }
  }
}

/** One frame of an animation. With `still`, the resting frame for reduced motion. */
export function frame(art: Art, mood: Scene, t: number, { seed = 0, still = false } = {}): Pixels {
  if (still) {
    const base: Pose = { x: LEFT, y: TOP };
    if (mood === "off") return draw(art, { ...base, grey: .85, alpha: .85, eyes: "closed" });
    if (mood === "setup") return draw(art, { ...base, ghost: .35, alpha: .9 });
    if (mood === "attn") return draw(art, { ...base, tint: .35 }, (() => { const p: Part[] = []; glyph(p, BANG, 3, 4, RED); return p; })());
    return draw(art, base);
  }
  const { tag, parts } = pose(art, mood, t, seed);
  return draw(art, tag, parts);
}
