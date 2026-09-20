"""Print a terminal sprite module sampled from the supplied reference image.

Usage: uv run --with pillow python docs/testing/sample_mascot.py IMAGE
No image or source files are modified. Sample 7x7 medians near the centers of
the reference's approximately 20-pixel blocks, then reduce noise to 24 colors.
"""
import json
import statistics
import sys

from PIL import Image


image = Image.open(sys.argv[1]).convert("RGB")
columns, rows = 28, 34
samples = []
for row in range(rows):
    for column in range(columns):
        x = round(352 + (column + .5) * 20.4)
        y = round(282 + (row + .5) * 20.2)
        pixels = [image.getpixel((x + dx, y + dy)) for dx in range(-3, 4) for dy in range(-3, 4)]
        samples.append(tuple(int(statistics.median(pixel[channel] for pixel in pixels)) for channel in range(3)))
sampled = Image.new("RGB", (columns, rows))
sampled.putdata(samples)
indexed = sampled.quantize(colors=24, dither=Image.Dither.NONE)
palette = indexed.getpalette()
symbols = "ABCDEFGHIJKLMNOPQRSTUVWX"
colors = {symbols[i]: "#%02x%02x%02x" % tuple(palette[i * 3:i * 3 + 3]) for i in range(24)}
data = list(indexed.tobytes())
sprite = ["".join(symbols[i] for i in data[row * columns:(row + 1) * columns]) for row in range(rows)]
source = '"""Pixel-center samples from the user-supplied blue droplet reference.\n\nReproduce with docs/testing/sample_mascot.py; no runtime image dependency.\n"""\n\n'
source += "PALETTE = " + repr(colors) + "\n\nPIXELS = (\n"
source += "".join("    " + repr(row) + ",\n" for row in sprite) + ")\n"
print(json.dumps({"source": source, "columns": columns, "rows": rows}))
