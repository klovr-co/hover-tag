"""Capture real Tag PTY output with fixture data; never contact Slack or start services.

uv run --with questionary==2.1.1 --with pyte --with pillow python \
    docs/testing/capture_tui.py before
Run again with `after` following UI edits. Images and raw ANSI go to .context/tui.
"""
from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import termios
import time

import pyte
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
SCENES = {
    "status": "from scripts import tag_display as d; d.backend_status=lambda _: ('Signed in · task not tested', True); d.summary('running', 'tag status', slack=True, memory=True, backend='codex')",
    "setup": "from scripts import setup_ui as u; u.screen(2, 'Which app should Tag use?'); u.choose('Slack app', ['Create a new Tag app', 'Use an existing app', 'Save and exit'])",
    "channels": "from scripts import setup_ui as u; u.screen(3, 'Where should Tag respond?', 'Slack connected'); u.checklist(['#general', '#engineering', '#design', '#product'], {1, 2})",
    "settings": "import tempfile; from pathlib import Path; from scripts.tag_control import settings_menu; fixture=tempfile.TemporaryDirectory(); settings_menu(Path(fixture.name))",
}


def capture(program: str, columns: int, rows: int) -> bytes:
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', rows, columns, 0, 0))
    env = dict(os.environ, TERM='xterm-256color', PROMPT_TOOLKIT_NO_CPR='1')
    env.pop('NO_COLOR', None)
    process = subprocess.Popen([sys.executable, '-c', program], cwd=ROOT,
                               stdin=slave, stdout=slave, stderr=slave, env=env)
    output = b''
    try:
        deadline = time.monotonic() + 6
        last_output = time.monotonic()
        while time.monotonic() < deadline:
            if select.select([master], [], [], .1)[0]:
                output += os.read(master, 65536)
                last_output = time.monotonic()
            elif output and time.monotonic() - last_output > .6:
                break
        if b'Traceback' in output or not output:
            raise RuntimeError(output.decode(errors='replace'))
        return output
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=3)
        os.close(master)
        os.close(slave)


def render(data: bytes, path: Path, columns: int, rows: int, font_path: str) -> None:
    screen = pyte.Screen(columns, rows)
    pyte.Stream(screen).feed(data.decode('utf-8', errors='replace'))
    font = ImageFont.truetype(font_path, 18)
    cell_width, cell_height, padding = 11, 26, 24
    image = Image.new('RGB', (columns * cell_width + 2 * padding, rows * cell_height + 2 * padding), '#11171c')
    draw = ImageDraw.Draw(image)
    palette = {'default': '#dce4e8', 'black': '#11171c', 'red': '#ef8686',
               'green': '#87d7af', 'brown': '#e5c07b', 'blue': '#83aef5',
               'magenta': '#c6a0ef', 'cyan': '#8acbd0', 'white': '#dce4e8',
               'brightblack': '#89949f', 'brightwhite': '#ffffff'}
    def color(value, background=False):
        if value == 'default' and background:
            return '#11171c'
        return palette.get(value, '#' + value if len(value) == 6 else '#dce4e8')
    glyphs = []
    for y in range(rows):
        for x in range(columns):
            char = screen.buffer[y][x]
            fg, bg = color(char.fg), color(char.bg, True)
            if char.reverse:
                fg, bg = bg, fg
            left, top = padding + x * cell_width, padding + y * cell_height
            draw.rectangle((left, top, left + cell_width, top + cell_height), fill=bg)
            glyphs.append((left, top, char.data, fg))
    for left, top, character, fg in glyphs:
        # Terminal block glyphs fill their cells, independent of font bearings.
        if character in {"▀", "▄", "█"}:
            start = top + cell_height // 2 if character == "▄" else top
            end = top + cell_height // 2 if character == "▀" else top + cell_height
            draw.rectangle((left, start, left + cell_width - 1, end - 1), fill=fg)
        else:
            draw.text((left, top), character, font=font, fill=fg)
    image.save(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['before', 'after'])
    parser.add_argument('--columns', type=int, default=90)
    parser.add_argument('--rows', type=int, default=28)
    parser.add_argument('--font', default='/System/Library/Fonts/Menlo.ttc')
    parser.add_argument('--render-only', action='store_true', help='Render saved ANSI without running Tag again')
    args = parser.parse_args()
    destination = ROOT / '.context/tui' / args.phase / str(args.columns)
    destination.mkdir(parents=True, exist_ok=True)
    for name, program in SCENES.items():
        if args.render_only:
            data = (destination / f'{name}.ansi').read_bytes()
        else:
            data = capture(program, args.columns, args.rows)
            (destination / f'{name}.ansi').write_bytes(data)
        render(data, destination / f'{name}.png', args.columns, args.rows, args.font)
        print(destination / f'{name}.png')


if __name__ == '__main__':
    main()
