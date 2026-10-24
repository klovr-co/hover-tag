"""Pixel-center samples from the user-supplied blue droplet reference.

The sprite lives in tag_mascot.json so the terminal, the Slack picture, the
menu bar icons and Tag.app all draw the same 28 × 34 map. Reproduce it with
docs/testing/sample_mascot.py; no runtime image dependency.
"""
import json
from pathlib import Path

_DATA = json.loads(Path(__file__).with_name("tag_mascot.json").read_text(encoding="utf-8"))

PALETTE = dict(_DATA["palette"])

PIXELS = tuple(_DATA["pixels"])
