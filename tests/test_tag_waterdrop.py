from __future__ import annotations

import hashlib
import statistics
import struct
import unittest
import zlib

from scripts import tag_waterdrop
from scripts.tag_mascot import PIXELS


def _pixels(png: bytes) -> list[bytes]:
    """Rows of RGB bytes from the unfiltered PNG render_png writes."""
    width, height = struct.unpack(">II", png[16:24])
    data, offset = b"", 8
    while offset < len(png):
        length, kind = struct.unpack(">I4s", png[offset:offset + 8])
        if kind == b"IDAT":
            data += png[offset + 8:offset + 8 + length]
        offset += 12 + length
    raw = zlib.decompress(data)
    stride = 1 + width * 3
    return [raw[row * stride + 1:(row + 1) * stride] for row in range(height)]


def _drawn_from_sprite(index: int) -> list[bytes]:
    """Paint the sprite the way Tag.app reads it: background, map letters, then marks."""
    sprite = tag_waterdrop.sprite(index)
    size, scale = tag_waterdrop.PICTURE_SIZE, tag_waterdrop.PICTURE_SCALE
    left = (size - len(PIXELS[0]) * scale) // 2
    top = (size - len(PIXELS) * scale) // 2
    colour = lambda value: bytes.fromhex(value[1:])
    cells = {}
    for row, line in enumerate(PIXELS):
        for column, key in enumerate(line):
            cells[column, row] = colour(sprite["palette"][key])
    for column, row, wide, high, value in sprite["marks"]:
        for y in range(row, row + high):
            for x in range(column, column + wide):
                cells[x, y] = colour(value)
    background = colour(sprite["background"])
    return [b"".join(cells.get(((x - left) // scale, (y - top) // scale), background) for x in range(size))
            for y in range(size)]


class WaterdropTests(unittest.TestCase):
    def test_sprite_draws_exactly_the_uploaded_slack_picture(self) -> None:
        # One look per signature and every element, highlight and background.
        for index in (*range(0, 960, 61), 1, 2, 3, 4, 959):
            with self.subTest(index=index):
                png = tag_waterdrop.render_png(tag_waterdrop.waterdrop_recipe("", index))
                self.assertEqual(_pixels(png), _drawn_from_sprite(index))

    def test_water_fire_and_soil_keep_the_pictures_existing_tags_wear(self) -> None:
        # Only metal and wood were recoloured; these digests predate the recolour.
        expected = {
            2: "6a9dfe6b69cce4086dc5477672111ab1b56182560aba61a812fa0429030cfa22",  # water
            3: "0eff7ab381e35dbb90307db7191f595f7eafca478a7a013ad644cdc965b71a24",  # fire
            4: "b29aad318f3d9264de1d74b5685ff3944f6ea39b892c1b9f9394e6bd01556105",  # soil
            62: "da79a98a8c4ef21054df7de2234fe545c3086901daf767fca4e12231c473c5b1",  # water
            958: "4df4eb95809474bc636b532eac5456c3e4aa869405912dfe85dc2753cd32d997",  # fire
            959: "8e58cd0d9cc3afd22a0a888d7d3d77d0c77e7985f7a3daff4e26e3ee0cbd5fb4",  # soil
        }
        self.assertEqual(expected, {index: hashlib.sha256(tag_waterdrop.render_png(
            tag_waterdrop.waterdrop_recipe("", index))).hexdigest() for index in expected})

    def test_metal_and_wood_outlines_keep_3_to_1_contrast(self) -> None:
        surfaces = ("#f6fafe", "#ffffff", "#0a1826", "#1a1d21")  # App light and dark, white, Slack dark.

        def luminance(colour: bytes) -> float:
            linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in (v / 255 for v in colour)]
            return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]

        def contrast(a: bytes, b: bytes) -> float:
            high, low = sorted((luminance(a), luminance(b)), reverse=True)
            return (high + .05) / (low + .05)

        inside = [[key not in "BCDEFG" for key in line] for line in PIXELS]
        edge = [(x, y) for y, line in enumerate(inside) for x, filled in enumerate(line) if filled and not all(
            0 <= x + dx < len(line) and 0 <= y + dy < len(inside) and inside[y + dy][x + dx]
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))]
        for index, element in ((0, "metal"), (1, "wood")):
            palette = tag_waterdrop._waterdrop_palette(tag_waterdrop.waterdrop_recipe("", index))
            for surface in surfaces:
                with self.subTest(element=element, surface=surface):
                    worst = statistics.median(contrast(palette[PIXELS[y][x]], bytes.fromhex(surface[1:])) for x, y in edge)
                    self.assertGreaterEqual(worst, 3)

    def test_sprite_names_its_element_and_every_map_letter(self) -> None:
        sprite = tag_waterdrop.sprite(60)  # Metal with rose cheeks.
        self.assertEqual("metal", sprite["element"])
        self.assertLessEqual(set("".join(PIXELS)), set(sprite["palette"]))
        self.assertEqual(2, len(sprite["marks"]))


if __name__ == "__main__":
    unittest.main()
