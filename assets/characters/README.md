# Tag cast · 角色介绍

Internal character and asset reference · September 2026

This is a creative reference, not a public documentation page. Names and element
pairings are agreed. Personality notes below are creative direction for future
assets—not employee biographies, job titles, or product capabilities.

## Cast at a glance

| Character | Portrait | Companion Tag | Element | Color | Current example role |
| --- | --- | --- | --- | --- | --- |
| **Maya** | <img src="./tag-maya-pixel.png" width="112" alt="Maya" /> | <img src="./tag-avatar-water-pixel.png" width="72" alt="Water Tag" /> | 水 Water | Blue · default | Getting started; follow-ups; collaboration |
| **Jules** | <img src="./tag-jules-pixel.png" width="112" alt="Jules" /> | <img src="./tag-avatar-soil-pixel.png" width="72" alt="Soil Tag" /> | 土 Soil | Yellow | Team planning and access examples |
| **Iris** | <img src="./tag-iris-pixel.png" width="112" alt="Iris" /> | <img src="./tag-avatar-metal-pixel.png" width="72" alt="Metal Tag" /> | 金 Metal | White / silver | File review; finding earlier decisions |
| **Rowan** | <img src="./tag-rowan-pixel.png" width="112" alt="Rowan" /> | <img src="./tag-avatar-wood-pixel.png" width="72" alt="Wood Tag" /> | 木 Wood | Green | Saving a decision in a workspace file |
| **Zara** | <img src="./tag-zara-pixel.png" width="112" alt="Zara" /> | <img src="./tag-avatar-fire-pixel.png" width="72" alt="Fire Tag" /> | 火 Fire | Red | Gmail and connected-tool examples |

## Maya · 水 / Water

**亲切的引路人 — the approachable guide.**

Maya makes the first step feel easy. Curious, friendly, and practical; her
examples help someone turn a loose request into something they can act on.

- Visual anchors: purple bob, side-swept fringe, warm skin, teal collared shirt.
- Signature acting: gentle head tilt, open attentive eyes, welcoming smile.
- Future variations: listening, explaining a first step, a small celebratory smile.
- Keep: the purple hair and teal clothing. Water is blue; it does not mean
  recoloring her hair or skin blue.
- Pair with: **Maya's Tag**, always Water blue.

## Jules · 土 / Soil

**踏实的协作者 — the dependable teammate.**

Jules gives shared work structure: what needs doing, who owns it, and what is
still missing. Warm and grounded rather than stern or managerial.

- Visual anchors: tousled brown hair, large dark glasses, mustard-yellow sweater.
- Signature acting: calm smile, attentive gaze, relaxed three-quarter portrait.
- Future variations: checking a list, thinking through a deadline, acknowledging a teammate.
- Keep: recognizable glasses and mustard knitwear; do not turn Jules into Rowan.
- Pair with: **Jules's Tag**, always Soil yellow.

## Iris · 金 / Metal

**清晰的审阅者 — the composed reviewer.**

Iris notices the unanswered question and brings clarity without making a fuss.
Precise, quietly confident, with a little dry wit.

- Visual anchors: silver-white bob, warm brown skin, structured ivory jacket.
- Signature acting: near-frontal upright pose, raised eyebrow, restrained half-smile.
- Future variations: evaluating a brief, pointing out a missing detail, a subtle nod.
- Keep: white/silver hair, ivory silhouette, and natural warm skin tones.
- Pair with: **Iris's Tag**, always Metal white/silver.
- Asset caveat: the current Metal Tag bitmap reads grey. White/silver is the
  intended palette; this reference records the current asset, not approval of
  its brightness. Any recolor should be reviewed before replacing it.

## Rowan · 木 / Wood

**轻松的探索者 — the easygoing explorer.**

Rowan is curious and resourceful, with a playful edge. A good fit for collecting
ideas, keeping useful notes, and building on something over time.

- Visual anchors: dark tousled wavy hair, light stubble, forest-green overshirt,
  sage inner shirt.
- Signature acting: turned shoulders, looking back toward the viewer, crooked grin.
- Future variations: jotting a note, discovering an idea, a thoughtful sideways glance.
- Keep: asymmetry and relaxed posture. Do not copy Maya's front-facing smile.
- Pair with: **Rowan's Tag**, always Wood green.
- Placement: the saved-decision example in **What Tag knows**. Getting started
  stays with Maya.

## Zara · 火 / Fire

**热情的推动者 — the expressive spark.**

Zara brings warmth and momentum. Outgoing and expressive, she makes communication
feel human without turning every moment into a celebration.

- Visual anchors: dark curly hair gathered high, warm brown skin, vivid red sweater.
- Signature acting: head tilted back, eyes closed in laughter, open smile, raised hand.
- Future variations: animated explanation, a friendly wave, focused enthusiasm.
- Keep: curls, red clothing, and expressive gestures; new scenes need not always laugh.
- Pair with: **Zara's Tag**, always Fire red.

## Shared visual rules

1. Use these existing portraits as identity references. Do not regenerate a
   character from their name or element alone.
2. Keep the pixel-art family: visible stepped edges, deliberate pixel clusters,
   warm character shading, readable faces. Avoid smooth vector or photoreal styles.
3. Match each person's clothing accents, background family, and companion Tag
   to their element. Preserve natural skin tones and identifying hair colors.
4. Keep distinct acting: Maya welcoming, Jules attentive, Iris composed, Rowan
   relaxed and asymmetrical, Zara expressive. Do not give all five the same pose.
5. For avatar exports, use a square canvas and leave headroom. Keep hair, gestures,
   and the Tag's floating droplet inside the frame. Inspect at 32 px and larger sizes.
6. Do not stretch or zoom-crop Tag sprites. The docs use containment and pixelated
   rendering; the removed 145% scale clipped the tops of the Tags.
7. Backgrounds may be transparent in source portraits. Composite them on the
   appropriate pale element background, not black. Jules currently has a baked-in
   yellow background; do not assume all files have the same alpha treatment.
8. These elements are a visual identity system, not different assistant powers,
   permission levels, or personality modes in the product.

### Current UI background tokens

These are CSS backplates, not exact sampled colors from the artwork.

| Metal | Wood | Water | Fire | Soil |
| --- | --- | --- | --- | --- |
| `#f3f3f1` | `#e4f2df` | `#e8f3fa` | `#fae5e2` | `#faf0ce` |

## Asset source of truth

- Human portraits: `assets/characters/tag-{maya,jules,iris,rowan,zara}-pixel.png`.
- Companion sprites: `assets/characters/tag-avatar-{water,soil,metal,wood,fire}-pixel.png`.
- Water currently uses `tag-avatar-water-pixel.png`, **not** legacy `tag-avatar-pixel.png`.
- The approved distinct-pose portraits for Iris, Rowan, and Zara are installed
  under their named files above. Earlier generic-pose generations are superseded.
- This folder in the Tag repository is the self-contained character reference.
- The separate Hover repository currently keeps website assets under `assets/`.
  Its `tag/scripts/prepare-assets.mjs` copies those into `tag/public/assets/`.
  Updating this reference does not automatically update the website: copy approved
  changes into Hover deliberately, then run its asset preparation script.
- Website employee-to-element assignments live in Hover\'s
  `tag/components/slack-example.tsx`.
- Keep new variants under new filenames for review; do not silently overwrite
  these identity references. Jun and Alex are not members of this cast.

## Brief template for future assets

> Create [asset / scene] featuring [name]. Use the attached current portrait as
> the identity reference. Preserve [hair, clothing, face, distinguishing details]
> and the established pixel-art rendering. Show [specific new action, expression,
> and pose]. Use [element] accents and a matching pale background, with natural
> skin tones. If a companion appears, use the attached [element] Tag sprite as
> its shape reference. Keep the full silhouette readable with safe margins.
> Do not copy another cast member's pose, add lettering, or change the identity.

Attach the named portrait and, when relevant, its matching Tag image. Describe
one distinct action per character in group scenes. Review face consistency,
element pairing, silhouette, and small-size readability before promoting a variant.
