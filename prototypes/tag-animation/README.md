# Tag animation prototype

A design prototype for animating the Tag waterdrop. It is not part of Tag.app.
Every frame is drawn in code from the pixel art in `assets/characters`, so the
drop can blink, hop, change colour and merge with other Tags while still
looking like the original art.

Open either page straight from disk:

- `palettes.html` compares the original and proposed palettes on light and dark
  surfaces, with the contrast of each drop's outline.
- `animations.html` shows every animation. It uses the proposed palette by
  default; the **Original** button (or `animations.html#original`) switches
  back for comparison.

Both pages are generated. Edit `palettes.py` or the templates in `src/`, then run:

```sh
python3 prototypes/tag-animation/build.py
```

`data/original-grids.json` is the five elements' art as it was before any
recolour, one colour per pixel. It is the source for both palettes.

## Palette decision (2026-10-13)

Wood and metal are recoloured. Water, fire and soil are unchanged.

| Element | Original | Proposed | Why |
| --- | --- | --- | --- |
| Wood | Neon green | Jade, shadows towards teal | The neon green almost disappears on light surfaces: its outline is 1.57:1 against the app's light panel. Jade clears 3:1 everywhere (worst 3.99:1) and reads as leaves rather than a highlighter. |
| Metal | Flat grey | Cool silver, stronger shine | Flat grey reads as disabled and looks like the offline state. Silver keeps the outline above 3:1 everywhere (worst 3.99:1, up from 3.72:1). |

Contrast is the median outline contrast against the app's light and dark
panels, white, and Slack's dark mode. A Tag is a graphic, so it needs at least
3:1 (WCAG 1.4.11). Deeper versions of jade and silver gain contrast on light
surfaces but drop below 3:1 on Slack's dark mode, so the balanced versions were
kept.

Both colours are derived from the water drop's own shading in OKLCH, so they
keep the same light and shadow as the other elements.

Open points:

- Soil's yellow is also below 3:1 on light surfaces (1.71:1). It was kept as
  asked; a darker soil outline would fix the contrast without changing the colour.
- Slack pictures are not drawn from these PNGs. Setup draws them with
  `branded_profile_icon` in `scripts/opentag_setup.py`, where metal is a
  pale pinkish white. Changing real Tags means changing that generator too.
- In the light banding style, water + soil and fire + wood mix to dull colours
  (near white and olive). Per-pair tuning is proposed but not done.
