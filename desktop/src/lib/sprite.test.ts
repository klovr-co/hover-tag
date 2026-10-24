import { describe, expect, test } from "vitest";
import mascot from "../../../scripts/tag_mascot.json";
import list from "../../../protocol/examples/list.json";
import { frame, LEFT, PERIOD, prepare, shift, TILE, TOP, type Mood, type Scene, type Sprite } from "./sprite";

const water = list.tags[0].picture!.sprite as Sprite;
const at = (pixels: Uint8ClampedArray, x: number, y: number) => {
  const k = (y * TILE + x) * 4;
  return "#" + [0, 1, 2].map((n) => pixels[k + n].toString(16).padStart(2, "0")).join("");
};

/** The Slack picture at one pixel per map cell: background, map letters, then the look's marks. */
function slackPicture(sprite: Sprite) {
  const cells = new Map<string, string>();
  mascot.pixels.forEach((row, y) => [...row].forEach((letter, x) => cells.set(`${x},${y}`, sprite.palette[letter])));
  for (const [column, row, wide, high, colour] of sprite.marks) {
    for (let y = row; y < row + high; y++) for (let x = column; x < column + wide; x++) cells.set(`${x},${y}`, colour);
  }
  return (x: number, y: number) => cells.get(`${x - LEFT},${y - TOP}`) ?? sprite.background;
}

describe("the waterdrop sprite", () => {
  test("its resting frame is the Slack picture, cell for cell, with or without marks", () => {
    const marked: Sprite = { ...water, marks: [[5, 21, 3, 2, "#ff8fb1"], [1, 11, 4, 2, "#36b4ff"]] };
    for (const sprite of [water, marked]) {
      const pixels = frame(prepare(sprite), "idle", 0, { still: true }), expected = slackPicture(sprite);
      for (let y = 0; y < TILE; y++) for (let x = 0; x < TILE; x++) expect(at(pixels, x, y), `${x},${y}`).toBe(expected(x, y));
    }
  });

  test("idle starts with open eyes, then blinks", () => {
    const art = prepare(water), eye = (t: number) => at(frame(art, "idle", t), LEFT + 8, TOP + 20);
    expect(eye(0)).toBe(water.palette.X);
    expect(eye(3.2)).not.toBe(water.palette.X);
  });

  test("every mood animates, and offline is grey", () => {
    const art = prepare(water);
    for (const mood of Object.keys(PERIOD) as Scene[]) {
      const frames = [0, .25, .5, .75].map((u) => frame(art, mood, PERIOD[mood] * u).join());
      expect(new Set(frames).size, mood).toBeGreaterThan(1);
    }
    const body = at(frame(art, "off", 0), LEFT + 13, TOP + 20);
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(body.slice(i, i + 2), 16));
    expect(Math.max(r, g, b) - Math.min(r, g, b)).toBeLessThan(40);
  });

  test("marks beside the drop move with it", () => {
    const art = prepare({ ...water, marks: [[1, 11, 1, 1, "#ff0000"]] });
    const rows = (pixels: Uint8ClampedArray) => [...Array(TILE).keys()].filter((y) => at(pixels, LEFT + 1, y) === "#ff0000");
    const resting = rows(frame(art, "idle", 0, { still: true }));
    expect(resting).toEqual([TOP + 11]);
    // Starting hops: at the top of the hop the mark is higher than at rest.
    expect(rows(frame(art, "start", 2.25))[0]).toBeLessThan(TOP + 11);
  });

  test("going to sleep and waking up play once, from one still frame to the other", () => {
    const art = prepare(water), still = (mood: Mood) => frame(art, mood, 0, { still: true }).join();
    expect(frame(art, "sleep", 0).join()).toBe(still("idle"));
    expect(frame(art, "sleep", PERIOD.sleep).join()).toBe(still("off"));
    expect(frame(art, "wake", 0).join()).toBe(still("off"));
    expect(frame(art, "wake", PERIOD.wake).join()).toBe(still("idle"));
  });

  test("only falling asleep and waking without Starting are played as changes", () => {
    expect(shift(null, "off")).toBeNull();
    expect(shift("idle", "off")).toBe("sleep");
    expect(shift("work", "off")).toBe("sleep");
    expect(shift("off", "idle")).toBe("wake");
    expect(shift("off", "start")).toBeNull();
    expect(shift("start", "idle")).toBeNull();
    expect(shift("idle", "idle")).toBeNull();
  });
});
