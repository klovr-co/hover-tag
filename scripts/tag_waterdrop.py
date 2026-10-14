"""The 960 waterdrop looks a Tag can wear, as a picture and as sprite data.

Setup uploads ``render_png`` as the Tag's Slack picture. ``tag list --json``
reports ``sprite`` for the same look, so Tag.app can animate exactly the drop
people see in Slack. Both are drawn from one recipe and the shared 28 × 34 map
in tag_mascot.json. Standard library only.
"""
from __future__ import annotations

import colorsys
import hashlib
import math
import struct
import zlib
from dataclasses import dataclass

try:
    from .tag_mascot import PALETTE as MASCOT_PALETTE, PIXELS as MASCOT_PIXELS
except ImportError:
    from tag_mascot import PALETTE as MASCOT_PALETTE, PIXELS as MASCOT_PIXELS


@dataclass(frozen=True)
class OklchRecolour:
    """Recolour the water drop's shading in OKLCH, as the animation prototype proposes.

    ``hue`` is the base OKLCH hue, ``chroma`` scales the colour, ``lightness``
    shifts it, ``shadow_turn`` turns the hue further in the shadows, and
    ``contrast`` stretches lightness around the mid tone.
    """
    hue: float
    chroma: float
    lightness: float
    shadow_turn: float
    contrast: float


@dataclass(frozen=True)
class WaterdropBody:
    name: str
    hue: float
    saturation: float = 1.0
    recolour: OklchRecolour | None = None


@dataclass(frozen=True)
class WaterdropBackground:
    name: str
    hue_offset: float
    saturation: float
    value: float


WATER_BODY = WaterdropBody("water", .57)
WATERDROP_BODIES = (
    # Cool silver with a stronger shine; the old pale pink read as disabled.
    WaterdropBody("metal", .58, .12, OklchRecolour(255, .12, .04, 12, 1.25)),
    # Jade with teal shadows; the old neon green faded on light surfaces.
    WaterdropBody("wood", .40, .62, OklchRecolour(150, .92, 0, 20, 1.05)),
    WATER_BODY,
    WaterdropBody("fire", .01, .92),
    WaterdropBody("soil", .13, .92),
)
DEFAULT_WATERDROP_ELEMENT = "water"
WATERDROP_BACKGROUNDS = (
    WaterdropBackground("mist", 0, .18, .96),
    WaterdropBackground("haze", -.02, .12, .99),
    WaterdropBackground("veil", .02, .08, 1.0),
)
WATERDROP_APPROVED_BACKGROUNDS = {
    element: ("mist", "haze", "veil")
    for element in ("metal", "wood", "water", "fire", "soil")
}
WATERDROP_HIGHLIGHTS = ("glass", "pearl", "glow", "frost")
WATERDROP_SIGNATURES = (
    "clean", "rose-cheeks", "peach-cheeks", "freckles",
    "north-sparkle", "east-sparkle", "west-sparkle", "twin-sparkles",
    "left-bubble", "right-bubbles", "twin-bubbles", "bubble-trail",
    "gold-crown", "side-stripe", "twin-dots", "heart-mark",
)
WATERDROP_BASE_COUNT = len(WATERDROP_BODIES) * 3 * len(WATERDROP_HIGHLIGHTS)
WATERDROP_SIGNATURE_COUNT = len(WATERDROP_SIGNATURES)
WATERDROP_RECIPE_COUNT = WATERDROP_BASE_COUNT * WATERDROP_SIGNATURE_COUNT
WATERDROP_ELEMENT_RECIPE_COUNT = WATERDROP_RECIPE_COUNT // len(WATERDROP_BODIES)

# The Slack picture: a 512 px square with the map drawn 12 px per cell, centred.
PICTURE_SIZE = 512
PICTURE_SCALE = 12


def waterdrop_recipe(seed: str, recipe_index: int | None = None) -> dict[str, object]:
    """Choose one five-element waterdrop with a subtle, deterministic signature."""
    if recipe_index is None:
        recipe_index = int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:8], "big")
    identity_index = recipe_index % WATERDROP_RECIPE_COUNT
    base_index = identity_index % WATERDROP_BASE_COUNT
    signature_index = identity_index // WATERDROP_BASE_COUNT
    body = WATERDROP_BODIES[base_index % len(WATERDROP_BODIES)]
    background_slot = (base_index // len(WATERDROP_BODIES)) % 3
    background_name = WATERDROP_APPROVED_BACKGROUNDS[body.name][background_slot]
    background = next(item for item in WATERDROP_BACKGROUNDS if item.name == background_name)
    highlight = WATERDROP_HIGHLIGHTS[base_index // (len(WATERDROP_BODIES) * 3)]
    return {
        "index": identity_index,
        "base_index": base_index,
        "signature_index": signature_index,
        "body": body,
        "background": background,
        "highlight": highlight,
        "signature": WATERDROP_SIGNATURES[signature_index],
    }


def _rgb_bytes(hue: float, saturation: float, value: float) -> bytes:
    red, green, blue = colorsys.hsv_to_rgb(hue % 1, max(0, min(1, saturation)), max(0, min(1, value)))
    return bytes(round(channel * 255) for channel in (red, green, blue))


# ---- OKLCH, for the recoloured elements -------------------------------------

WATER_OKLCH_HUE = 245.0


def _linear(channel: float) -> float:
    return channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4


def _gamma(channel: float) -> float:
    return 12.92 * channel if channel <= .0031308 else 1.055 * channel ** (1 / 2.4) - .055


def _to_oklch(red: float, green: float, blue: float) -> tuple[float, float, float]:
    r, g, b = _linear(red), _linear(green), _linear(blue)
    l = (.4122214708 * r + .5363325363 * g + .0514459929 * b) ** (1 / 3)
    m = (.2119034982 * r + .6806995451 * g + .1073969566 * b) ** (1 / 3)
    s = (.0883024619 * r + .2817188376 * g + .6299787005 * b) ** (1 / 3)
    lightness = .2104542553 * l + .793617785 * m - .0040720468 * s
    a = 1.9779984951 * l - 2.428592205 * m + .4505937099 * s
    b_axis = .0259040371 * l + .7827717662 * m - .808675766 * s
    return lightness, math.hypot(a, b_axis), (math.degrees(math.atan2(b_axis, a)) + 360) % 360


def _oklch_linear_rgb(lightness: float, chroma: float, hue: float) -> list[float]:
    a, b = chroma * math.cos(math.radians(hue)), chroma * math.sin(math.radians(hue))
    l = (lightness + .3963377774 * a + .2158037573 * b) ** 3
    m = (lightness - .1055613458 * a - .0638541728 * b) ** 3
    s = (lightness - .0894841775 * a - 1.291485548 * b) ** 3
    return [4.0767416621 * l - 3.3077115913 * m + .2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - .3413193965 * s,
            -.0041960863 * l - .7034186147 * m + 1.707614701 * s]


def _from_oklch(lightness: float, chroma: float, hue: float) -> bytes:
    lightness = min(.99, max(.05, lightness))
    while chroma > 0 and any(v < -1e-4 or v > 1 + 1e-4 for v in _oklch_linear_rgb(lightness, chroma, hue)):
        chroma -= .002  # Reduce chroma until it fits sRGB, keeping the hue.
    return bytes(round(min(1, max(0, _gamma(max(0, v)))) * 255)
                 for v in _oklch_linear_rgb(lightness, max(chroma, 0), hue))


def _recoloured(red: float, green: float, blue: float, recipe: OklchRecolour) -> bytes:
    lightness, chroma, hue = _to_oklch(red, green, blue)
    sparkle = lightness > .93  # Near-white sparkles stay light.
    new_lightness = lightness if sparkle else .72 + (lightness - .72) * recipe.contrast + recipe.lightness
    turn = ((hue - WATER_OKLCH_HUE + 540) % 360 - 180) * .6 + recipe.shadow_turn * max(0, .72 - lightness) * 3
    return _from_oklch(new_lightness, chroma * recipe.chroma * (.6 if sparkle else 1), (recipe.hue + turn) % 360)


def _body_color(value: str, body: WaterdropBody) -> bytes:
    if body.recolour is not None:
        # Start from the finished water drop, as the prototype recolours the water art.
        water = _body_color(value, WATER_BODY)
        return _recoloured(*(channel / 255 for channel in water), body.recolour)
    red, green, blue = (int(value[index:index + 2], 16) / 255 for index in (1, 3, 5))
    original_hue, saturation, brightness = colorsys.rgb_to_hsv(red, green, blue)
    base_hue = colorsys.rgb_to_hsv(0x20 / 255, 0xCA / 255, 0xFE / 255)[0]
    hue_delta = ((original_hue - base_hue + .5) % 1) - .5
    return _rgb_bytes(body.hue + hue_delta * .24, saturation * body.saturation, brightness)


def _waterdrop_palette(recipe: dict[str, object]) -> dict[str, bytes]:
    body = recipe["body"]
    background = recipe["background"]
    assert isinstance(body, WaterdropBody) and isinstance(background, WaterdropBackground)
    background_color = _rgb_bytes(
        body.hue + background.hue_offset,
        background.saturation * body.saturation,
        background.value,
    )
    palette = {
        key: background_color if key in "BCDEFG" else _body_color(value, body)
        for key, value in MASCOT_PALETTE.items()
    }
    highlight = recipe["highlight"]
    if highlight == "glass":
        palette["A"] = bytes((250, 253, 253))
    elif highlight == "pearl":
        palette["A"] = bytes((255, 247, 219))
    elif highlight == "glow":
        palette["A"] = _rgb_bytes(body.hue + .08, .18, 1)
    else:
        palette["A"] = _rgb_bytes(body.hue + .50, .12, .98)
    return palette


def _signature_cells(recipe: dict[str, object], palette: dict[str, bytes]) -> list[tuple[int, int, int, int, bytes]]:
    """The signature's marks as (column, row, cells wide, cells high, colour), painted in order."""
    body = recipe["body"]
    assert isinstance(body, WaterdropBody)
    signature = recipe["signature"]
    cells: list[tuple[int, int, int, int, bytes]] = []

    def paint_cell(column: int, row: int, color: bytes, *, cells_wide: int = 1, cells_high: int = 1) -> None:
        cells.append((column, row, cells_wide, cells_high, color))

    def sparkle(column: int, row: int, color: bytes) -> None:
        """Paint a four-cell-wide mark that survives Slack-size downsampling."""
        paint_cell(column, row - 1, color, cells_wide=2)
        paint_cell(column - 1, row, color, cells_wide=4, cells_high=2)
        paint_cell(column, row + 2, color, cells_wide=2)

    def bubble_mark(column: int, row: int) -> None:
        """Paint a small diamond bubble with a readable white glint."""
        paint_cell(column + 1, row, bubble, cells_wide=2)
        paint_cell(column, row + 1, bubble, cells_wide=4, cells_high=2)
        paint_cell(column + 1, row + 3, bubble, cells_wide=2)
        paint_cell(column + 1, row + 1, palette["A"])

    cheek = _rgb_bytes(body.hue + .38, .48, 1)
    peach = _rgb_bytes(.04, .38, 1)
    bubble = _rgb_bytes(body.hue + .04, .72, .96)
    accent = _rgb_bytes(body.hue + .38, .78, .94)
    gold = _rgb_bytes(.13, .88, 1)
    if signature == "rose-cheeks":
        paint_cell(5, 21, cheek, cells_wide=3, cells_high=2)
        paint_cell(20, 21, cheek, cells_wide=3, cells_high=2)
    elif signature == "peach-cheeks":
        paint_cell(5, 21, peach, cells_wide=3, cells_high=2)
        paint_cell(20, 21, peach, cells_wide=3, cells_high=2)
    elif signature == "freckles":
        for column, row in ((6, 21), (9, 22), (17, 22), (20, 21)):
            paint_cell(column, row, cheek, cells_wide=2)
    elif signature == "north-sparkle":
        sparkle(24, 9, palette["A"])
    elif signature == "east-sparkle":
        sparkle(24, 16, palette["A"])
    elif signature == "west-sparkle":
        sparkle(2, 14, palette["A"])
    elif signature == "twin-sparkles":
        sparkle(2, 14, palette["A"])
        sparkle(24, 9, palette["A"])
    elif signature == "left-bubble":
        bubble_mark(1, 11)
    elif signature == "right-bubbles":
        bubble_mark(23, 13)
    elif signature == "twin-bubbles":
        bubble_mark(1, 12)
        bubble_mark(23, 14)
    elif signature == "bubble-trail":
        bubble_mark(1, 16)
        paint_cell(3, 13, bubble, cells_wide=2, cells_high=2)
        paint_cell(5, 10, bubble, cells_wide=2, cells_high=2)
    elif signature == "gold-crown":
        paint_cell(11, 13, gold, cells_wide=2, cells_high=3)
        paint_cell(14, 12, gold, cells_wide=2, cells_high=4)
        paint_cell(17, 13, gold, cells_wide=2, cells_high=3)
        paint_cell(11, 16, gold, cells_wide=8, cells_high=2)
    elif signature == "side-stripe":
        paint_cell(3, 16, accent, cells_wide=3, cells_high=2)
        paint_cell(4, 18, accent, cells_wide=4, cells_high=2)
        paint_cell(5, 20, accent, cells_wide=4, cells_high=2)
    elif signature == "twin-dots":
        paint_cell(7, 14, gold, cells_wide=3, cells_high=3)
        paint_cell(19, 16, accent, cells_wide=3, cells_high=3)
    elif signature == "heart-mark":
        paint_cell(16, 23, accent, cells_wide=2, cells_high=2)
        paint_cell(19, 23, accent, cells_wide=2, cells_high=2)
        paint_cell(16, 25, accent, cells_wide=5, cells_high=2)
        paint_cell(17, 27, accent, cells_wide=3)
        paint_cell(18, 28, accent)
    return cells


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def render_png(recipe: dict[str, object]) -> bytes:
    """The 512 px Slack picture for one look."""
    width = height = PICTURE_SIZE
    scale = PICTURE_SCALE
    sprite_width, sprite_height = len(MASCOT_PIXELS[0]), len(MASCOT_PIXELS)
    left = (width - sprite_width * scale) // 2
    top = (height - sprite_height * scale) // 2
    palette = _waterdrop_palette(recipe)
    background = palette["E"]
    rows = []
    for y in range(height):
        source_y = (y - top) // scale
        row = bytearray(background * width)
        if 0 <= source_y < sprite_height:
            for source_x, key in enumerate(MASCOT_PIXELS[source_y]):
                start = left + source_x * scale
                row[start * 3:(start + scale) * 3] = palette[key] * scale
        rows.append(row)
    for column, row_index, cells_wide, cells_high, color in _signature_cells(recipe, palette):
        x_start, y_start = left + column * scale, top + row_index * scale
        for pixel_y in range(max(0, y_start), min(height, y_start + cells_high * scale)):
            start = max(0, x_start) * 3
            end = min(width, x_start + cells_wide * scale) * 3
            rows[pixel_y][start:end] = color * ((end - start) // 3)
    filtered_rows = [b"\x00" + bytes(row) for row in rows]
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(b"".join(filtered_rows), level=9))
        + _png_chunk(b"IEND", b"")
    )


def sprite(recipe_index: int) -> dict[str, object]:
    """What Tag.app needs to draw this look: colours per map letter and the signature marks.

    ``palette`` maps every letter in tag_mascot.json to a colour; letters B–G are
    the background. ``marks`` are (column, row, cells wide, cells high, colour)
    in map cells, painted in order over the drop; some sit outside the drop.
    """
    recipe = waterdrop_recipe("", recipe_index)
    palette = _waterdrop_palette(recipe)
    body = recipe["body"]
    assert isinstance(body, WaterdropBody)
    return {
        "element": body.name,
        "background": "#" + palette["E"].hex(),
        "palette": {key: "#" + color.hex() for key, color in palette.items()},
        "marks": [[column, row, wide, high, "#" + color.hex()]
                  for column, row, wide, high, color in _signature_cells(recipe, palette)],
    }
