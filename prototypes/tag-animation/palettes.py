#!/usr/bin/env python3
# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""The Tag waterdrop palettes for the animation prototype, and their contrast.

`original` is the art in assets/characters as it was before the recolour.
`proposed` keeps water, fire and soil and recolours wood and metal from the
water drop's shading, so every element keeps the same light and shadow.
Standard library only.
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
N = 42
# Pixels inside the drop that the art leaves as background: its sparkles.
HOLES = ((22, 8), (21, 9), (11, 21), (25, 23))
BACKGROUND = {
    "original": {"water": "#ebf4ff", "fire": "#fff0eb", "wood": "#ebffec", "metal": "#f5f5f5", "soil": "#fffceb"},
    "proposed": {"water": "#ebf4ff", "fire": "#fff0eb", "wood": "#eef5f0", "metal": "#f2f3f4", "soil": "#fffceb"},
}
# Where Tags are seen: the app's frosted panel in each theme, plain white, and Slack's dark mode.
SURFACES = {
    "App, light": "#f6fafe",
    "White": "#ffffff",
    "App, dark": "#0a1826",
    "Slack, dark": "#1a1d21",
}
# A Tag is a graphic, so its outline needs 3:1 against what it sits on (WCAG 1.4.11).
MIN_CONTRAST = 3.0

# Recolour recipe, in OKLCH: base hue, chroma scale, lightness shift,
# extra hue turn in the shadows, and lightness contrast around the mid tone.
RECOLOUR = {
    "wood": dict(hue=150, chroma=.92, lightness=0, shadow_turn=20, contrast=1.05),    # jade, shadows towards teal
    "metal": dict(hue=255, chroma=.12, lightness=.04, shadow_turn=12, contrast=1.25),  # cool silver, stronger shine
}
WATER_HUE = 245.0


def _linear(c: float) -> float:
    return c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4


def _gamma(c: float) -> float:
    return 12.92 * c if c <= .0031308 else 1.055 * c ** (1 / 2.4) - .055


def to_oklch(hex_colour: str) -> tuple[float, float, float]:
    r, g, b = (_linear(int(hex_colour[i:i + 2], 16) / 255) for i in (1, 3, 5))
    l = (.4122214708 * r + .5363325363 * g + .0514459929 * b) ** (1 / 3)
    m = (.2119034982 * r + .6806995451 * g + .1073969566 * b) ** (1 / 3)
    s = (.0883024619 * r + .2817188376 * g + .6299787005 * b) ** (1 / 3)
    L = .2104542553 * l + .793617785 * m - .0040720468 * s
    A = 1.9779984951 * l - 2.428592205 * m + .4505937099 * s
    B = .0259040371 * l + .7827717662 * m - .808675766 * s
    return L, math.hypot(A, B), (math.degrees(math.atan2(B, A)) + 360) % 360


def _rgb(L: float, C: float, h: float) -> list[float]:
    A, B = C * math.cos(math.radians(h)), C * math.sin(math.radians(h))
    l = (L + .3963377774 * A + .2158037573 * B) ** 3
    m = (L - .1055613458 * A - .0638541728 * B) ** 3
    s = (L - .0894841775 * A - 1.291485548 * B) ** 3
    return [4.0767416621 * l - 3.3077115913 * m + .2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - .3413193965 * s,
            -.0041960863 * l - .7034186147 * m + 1.707614701 * s]


def from_oklch(L: float, C: float, h: float) -> str:
    L = min(.99, max(.05, L))
    while C > 0 and any(v < -1e-4 or v > 1 + 1e-4 for v in _rgb(L, C, h)):
        C -= .002  # reduce chroma until it fits sRGB, keeping the hue
    return "#" + "".join("%02x" % round(min(1, max(0, _gamma(max(0, v)))) * 255) for v in _rgb(L, max(C, 0), h))


def recolour(water: list[list[str | None]], recipe: dict) -> list[list[str | None]]:
    cache: dict[str, str] = {}

    def convert(colour: str) -> str:
        if colour not in cache:
            L, C, h = to_oklch(colour)
            sparkle = L > .93  # near-white sparkles stay light
            L2 = L if sparkle else .72 + (L - .72) * recipe["contrast"] + recipe["lightness"]
            turn = ((h - WATER_HUE + 540) % 360 - 180) * .6 + recipe["shadow_turn"] * max(0, .72 - L) * 3
            cache[colour] = from_oklch(L2, C * recipe["chroma"] * (.6 if sparkle else 1), (recipe["hue"] + turn) % 360)
        return cache[colour]

    return [[None if c is None else convert(c) for c in row] for row in water]


def luminance(hex_colour: str) -> float:
    r, g, b = (_linear(int(hex_colour[i:i + 2], 16) / 255) for i in (1, 3, 5))
    return .2126 * r + .7152 * g + .0722 * b


def contrast(a: str, b: str) -> float:
    x, y = sorted((luminance(a), luminance(b)), reverse=True)
    return (x + .05) / (y + .05)


def outline(grid: list[list[str | None]]) -> list[str]:
    inside = lambda x, y: 0 <= x < N and 0 <= y < N and grid[y][x] is not None
    return [grid[y][x] for y in range(N) for x in range(N) if inside(x, y)
            and not all(inside(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))]


def contrast_table(grids: dict) -> dict:
    """Median contrast of each drop's outline and body against each surface."""
    table = {}
    for element, grid in grids.items():
        edge, body = outline(grid), [c for row in grid for c in row if c]
        table[element] = {
            name: {"outline": round(statistics.median(contrast(c, bg) for c in edge), 2),
                   "body": round(statistics.median(contrast(c, bg) for c in body), 2)}
            for name, bg in SURFACES.items()
        }
    return table


def encode(grid: list[list[str | None]], background: str) -> dict:
    """Pixel rows as letters into a small palette, as the prototype pages read them."""
    grid = [row[:] for row in grid]
    for x, y in HOLES:
        grid[y][x] = background
    pal = sorted({c for row in grid for c in row if c})
    return {"pal": pal, "rows": ["".join("." if c is None else chr(65 + pal.index(c)) for c in row) for row in grid]}


def build() -> dict:
    original = json.loads((HERE / "data" / "original-grids.json").read_text(encoding="utf-8"))
    proposed = dict(original)
    for element, recipe in RECOLOUR.items():
        proposed[element] = recolour(original["water"], recipe)
    palettes = {"original": original, "proposed": proposed}
    return {
        "elements": list(original),
        "changed": list(RECOLOUR),
        "surfaces": SURFACES,
        "min_contrast": MIN_CONTRAST,
        "art": {name: {el: encode(grid, BACKGROUND[name][el]) for el, grid in grids.items()}
                for name, grids in palettes.items()},
        "contrast": {name: contrast_table(grids) for name, grids in palettes.items()},
    }


if __name__ == "__main__":
    data = build()
    for name, table in data["contrast"].items():
        for element, row in table.items():
            worst = min(v["outline"] for v in row.values())
            print(f"{name:9s} {element:6s} worst outline {worst:.2f} {'ok' if worst >= MIN_CONTRAST else 'LOW'}")
