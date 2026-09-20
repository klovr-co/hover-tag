"""Slack CLI get-manifest hook. Emits only the project manifest, never secrets."""
from pathlib import Path
import sys


if __name__ == "__main__":
    sys.stdout.write(Path("manifest.json").read_text(encoding="utf-8"))
