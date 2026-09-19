# Terminal UI comparison

The status summary, setup prompts, channel checklist, and settings menu share a
cyan accent, muted section labels, and a clear keyboard focus marker. A pale-blue
banner with navy lettering and a smiling pixel droplet translates the supplied
mascot reference. The 28 × 34 map is sampled from the centers of the reference's
approximately 20-pixel blocks, with median sampling and a 24-color palette to
reduce image noise. It preserves the source placement rather than inventing a
new sprite. Half-block characters render two pixel rows per terminal line.
The banner samples that map at 13 × 14, compensating for tall terminal cells
while keeping its total height to 9 text rows.
Checked channels use cyan; green and amber retain their health meanings. The
artwork is omitted from plain and `NO_COLOR` output. Narrow terminals use a small
wordmark, and the mascot is omitted when there is insufficient room beside it.
Setup shows
the current stage and switches to a compact step label below 64 terminal columns.
Settings supports arrow-key navigation, including choosing the agent, while plain
terminals retain numbered input. `q`, Ctrl-C, and Ctrl-D leave settings cleanly.

The interface retains Questionary because setup hands the terminal to Slack CLI
and other interactive commands. The Textual skill informed layout, focus, and
keyboard accessibility; this change does not add a Textual runtime dependency.

## Capture actual terminal output

The capture helper uses fixture data with the production rendering functions.
It does not connect to Slack, inspect real credentials, or start services.
It captures ANSI from a real PTY, interprets it with `pyte`, and rasterizes terminal
cells with Pillow. Images are terminal renderings, not generated mockups or
desktop screenshots. Both versions use the same viewport, font, and colors.

Before changing the UI, run:

```sh
uv run --with questionary==2.1.1 --with pyte --with pillow \
  python docs/testing/capture_tui.py before
```

After changing the UI, run:

```sh
uv run --with questionary==2.1.1 --with pyte --with pillow \
  python docs/testing/capture_tui.py after --rows 44
uv run --with questionary==2.1.1 --with pyte --with pillow \
  python docs/testing/capture_tui.py after --columns 48 --rows 44
```

Outputs are under `.context/tui/{before,after}/{columns}/`. Each PNG has a raw ANSI
transcript beside it. `--render-only` re-renders saved transcripts without running
the current code, preserving the original before state. On Linux, supply a local
monospace font with `--font`; the PTY helper requires a POSIX host.

For this redesign, `.context/tui/comparison.html` displays all four before/after
pairs and the narrow-terminal checks. The original 28-row baseline transcripts
are rendered with blank padding on 44-row canvases to match the taller new UI.

Reproduce the mascot's data module by running `docs/testing/sample_mascot.py`
with the supplied reference image path. The helper prints source data without
writing files; only this development helper needs Pillow. The runtime imports
the resulting palette and grid from `scripts/tag_mascot.py`.
