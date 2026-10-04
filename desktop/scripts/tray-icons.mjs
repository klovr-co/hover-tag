// Monochrome tray masks from the TUI's pixel map: outline offline, filled online.
// Keep the original narrow eyes and small smile; macOS recolors the alpha mask.
import { execFileSync } from "node:child_process";
import { copyFile, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const desktop = fileURLToPath(new URL("../", import.meta.url));
const pixels = JSON.parse(execFileSync("python3", ["-c",
  "import json, runpy, sys; print(json.dumps(runpy.run_path(sys.argv[1])['PIXELS']))",
  join(desktop, "../scripts/tag_mascot.py")], { encoding: "utf8" }));
const width = 28, height = 34;
if (pixels.length !== height || pixels.some(row => row.length !== width)) {
  throw new Error("Review the tray layout when the TUI sprite dimensions change");
}

// Flood out only the surrounding banner background, preserving pale eye glints.
const background = new Set("BCDEFG");
const transparent = new Set();
const pending = [];
for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
  if (x === 0 || y === 0 || x === width - 1 || y === height - 1) pending.push([x, y]);
}
while (pending.length) {
  const [x, y] = pending.pop();
  const key = y * width + x;
  if (x < 0 || y < 0 || x >= width || y >= height || transparent.has(key)
    || !background.has(pixels[y][x])) continue;
  transparent.add(key);
  pending.push([x - 1, y], [x + 1, y], [x, y - 1], [x, y + 1]);
}
const inside = (x, y) => x >= 0 && y >= 0 && x < width && y < height
  && !transparent.has(y * width + x);

// Two source pixels give the offline silhouette a legible 1pt inner outline.
const neighbors = [];
for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) {
  if (dx * dx + dy * dy <= 4) neighbors.push([dx, dy]);
}
const border = (x, y) => neighbors.some(([dx, dy]) => !inside(x + dx, y + dy));
const mascot = (online, color) => {
  const rects = [];
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    if (!inside(x, y)) continue;
    const edge = border(x, y);
    // X is the original navy face color; exclude the silhouette's dark rim.
    const face = pixels[y][x] === "X" && !edge;
    if (online ? !face : edge || face) {
      rects.push(`<rect x="${x + 4}" y="${y + 1}" width="1" height="1"/>`);
    }
  }
  return `<svg xmlns="http://www.w3.org/2000/svg" width="36" height="36" viewBox="0 0 36 36" shape-rendering="crispEdges"><g fill="${color}">${rects.join("")}</g></svg>`;
};

const temporary = await mkdtemp(join(tmpdir(), "tag-tray-icons-"));
try {
  for (const [name, online, color] of [["tray", false, "#000"], ["tray-online", true, "#000"],
    ["tray-light", false, "#fff"], ["tray-light-online", true, "#fff"]]) {
    const source = join(temporary, `${name}.svg`);
    await writeFile(source, mascot(online, color));
    execFileSync(process.execPath, [join(desktop, "node_modules/@tauri-apps/cli/tauri.js"),
      "icon", source, "--png", "36", "--output", temporary], { stdio: "inherit" });
    await copyFile(join(temporary, "36x36.png"), join(desktop, "src-tauri/icons", `${name}.png`));
  }
} finally {
  await rm(temporary, { recursive: true, force: true });
}
