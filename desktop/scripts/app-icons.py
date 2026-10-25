# /// script
# requires-python = ">=3.10"
# dependencies = ["Pillow==11.3.0"]
# ///
"""Regenerate desktop icons: uv run desktop/scripts/app-icons.py."""

from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw


def main():
    desktop = Path(__file__).resolve().parents[1]
    icons = desktop / "src-tauri/icons"
    artwork = desktop.parent / "assets/branding/tag-icon.png"

    # The original avatar fills only 55% of its tile. A centered 320px crop
    # enlarges it 1.6x without clipping its silhouette or detached droplet.
    with Image.open(artwork) as original:
        if original.size != (512, 512):
            raise ValueError("Review the avatar crop when replacing the 512px artwork")
        tile = original.convert("RGBA").crop((96, 96, 416, 416))
    tile = tile.resize((824, 824), Image.Resampling.LANCZOS)

    # Preserve the macOS tile footprint and rounded corners. Supersample the
    # mask so the transparent edge stays smooth in the exported icon sizes.
    mask = Image.new("L", (824 * 4, 824 * 4))
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 3295, 3295), radius=184 * 4, fill=255)
    tile.putalpha(mask.resize(tile.size, Image.Resampling.LANCZOS))
    source = Image.new("RGBA", (1024, 1024))
    source.alpha_composite(tile, (100, 100))

    # Generate in a temporary folder to keep mobile outputs out of the desktop
    # repository and leave the independently drawn menu bar icons untouched.
    with tempfile.TemporaryDirectory(prefix="tag-app-icons-") as temporary:
        generated = Path(temporary)
        source_path = generated / "source-1024.png"
        source.save(source_path)
        subprocess.run(
            ["node", str(desktop / "node_modules/@tauri-apps/cli/tauri.js"),
             "icon", str(source_path), "--output", str(generated)],
            cwd=desktop, check=True,
        )
        for path in generated.iterdir():
            if path.is_file() and path.suffix in {".png", ".icns", ".ico"}:
                shutil.copyfile(path, icons / path.name)


if __name__ == "__main__":
    main()
