# Box panel: contract, renderer and `camera_boxes`

Work-order items B1 and B2 of
[lane B](../live-capture.md#lane-b-the-box-panel): the cloud-side pieces of
the head-fixed box panel (Level 1 of the
[box display scope change](../live-capture.md#box-display-scope-change-adopted-2026-11-24)).
A box and a name are drawn around each recognised object on a transparent
panel fixed in front of the head.

**Status: cloud-checked only, and selected by nothing.** These are a
contract, a pure renderer and a pure mapping, tested on generated boxes. No
stage, command or bridge mode uses them: the bridge's second geometry is item
B3, the wearer's alignment step is B4 and `camera_hud`'s layout is B5. Nothing
here displays anything, opens a gate or reads calibration data. The strip
(`fixed-edge-hud-v1`, [row selection](hud-runtime.md#row-selection)) is
unchanged, byte for byte, and stays the names-only fallback.

| Piece | Where |
| --- | --- |
| Geometry `head-fixed-box-panel-v1` (`BoxPanelConfig`, `BOX_PANEL_CONFIG`), `PanelBox`, `panel_box_valid`, `select_boxes` | `sightcircuit/hud/contract.py` |
| The name font: hand-drawn glyph bitmaps for the 65 characters a label can hold | `sightcircuit/hud/box_font.py` |
| Renderer `draw_boxes(boxes, config)` and its geometry `layout(boxes, config)` | `sightcircuit/hud/box_render.py` |
| `Alignment`, `camera_boxes`, `panel_point`, `boxes_report` | `sightcircuit/hud/boxes.py` |
| Tests | `tests/test_hud_box_panel.py`, `tests/test_hud_camera_boxes.py` |

## The geometry

`head-fixed-box-panel-v1` is named in the style of `fixed-edge-hud-v1`: what
it is fixed to (the head, which is Level 1's claim and its limit), what it is
(a box panel) and a version, so a Level 2 panel placed in the room is a new
name and not a silent change of this one. `config_digest(BOX_PANEL_CONFIG)`
pins every field below, drawing style, colours and the font's glyphs
included.

A configuration checks itself when it is built (`box_panel_config_invalid`),
so the selector and the renderer rely on one rule and neither re-checks:
whole positive sizes within the bridge's `MAX_SIDE`; the placement within
`BOX_PANEL_LIMITS`, the contract's copy of the bridge's panel limits (a test
keeps it equal to `overlay.PANEL_LIMITS`); at most 16 boxes; a smallest box
with a transparent inside; the font this checkout holds; colours that are
never the transparent pixel's; and a panel large enough that a name plate
can always avoid its own box (at least 416 × 80 with these name sizes).

| Field | Value | Meaning |
| --- | --- | --- |
| `width_px`, `height_px` | 512 × 512 | the panel image |
| `panel_width_m`, `distance_m`, `x_m`, `y_m` | 0.9, 0.75, 0, 0 | head-fixed placement: centred in the view, 0.75 m ahead, 0.9 m wide and (square) as tall |
| `opacity` | 1.0 | the overlay's own alpha; transparency is per pixel |
| `max_boxes` | 5 | the cap |
| `outline_px`, `casing_px` | 2, 1 | a bright core between two dark lines: a 4-pixel band |
| `min_box_px` | 12 | the smallest box drawn |
| `name_font`, `name_font_sha256` | `sightcircuit-cell-5x9-v1` and the digest of its glyphs | the project's own bitmap font |
| `name_scale`, `name_pad_px`, `name_max_px` | 2, 3, 190 | panel pixels per glyph cell, the plate's padding, the widest name (16 characters) |
| `outline_rgb`, `casing_rgb`, `plate_rgb`, `name_rgb` | (130, 230, 70), (16, 16, 16), (16, 16, 16), (235, 235, 235) | colours, as R, G, B |

### Why these numbers

None of them is a measurement. Each is a cloud choice with its reason, to be
looked at on the Frame in the retargeted displayed steps.

- **512 × 512 pixels.** The largest square the pipe protocol takes today:
  `MAX_SIDE` is 512, so no limit of the bridge has to move. Square because
  the camera image is square (2464 × 2464) and every scan window is a square
  fraction of it; a region of another shape still fills the panel, since the
  mapping scales each axis by itself.
- **0.9 m wide at 0.75 m: about 62° across.** The camera's field of view is
  not measured, so the panel's angular size cannot be derived from it and is
  not a calibration: the wearer's [alignment](#the-alignment) scales and
  moves the region inside the panel. The panel only has to be large enough to
  hold the region of view where the wearer looks and small enough to keep
  names readable. 2·atan(0.45 / 0.75) is 61.9°, the middle of the view. If
  L-D1b's region turns out wider than that, the alignment's scale goes above
  1 and boxes near the region's edge are cut at the panel's edge; the
  remedy is a `-v2` with other numbers, not a change to this one.
- **Pixels per degree and names.** 512 pixels over 61.9° is 8.3 pixels per
  degree on average and 7.45 at the centre, where a flat panel has the
  fewest. A glyph cell is 5 × 9 at 2 panel pixels per cell: capitals and
  digits are 14 pixels tall, 1.9° at the centre; a small letter's body is
  10 pixels, 1.3°; one stroke is 2 pixels, 0.27°. The outline band is 4
  pixels: about half a degree. A larger panel in degrees or a smaller one in
  pixels would push small letters towards one degree and under; a much
  smaller panel in degrees would not cover a useful region.
- **0.75 m ahead, not the strip's 1.25 m.** The panel is drawn for both
  eyes at its own distance, and the acceptance object is held within 1 m. A
  box drawn at 1.25 m around a bottle at 0.5 m differs from it in vergence
  by about 4.3° (0.063 m between the eyes × (1/0.5 − 1/1.25) m⁻¹), which is
  seen double; at 0.75 m the difference is 2.4° for an object at 0.5 m and
  1.2° at 1 m. This is arithmetic with an assumed eye separation, not an
  observation. Width and distance lie inside the bridge's existing panel
  limits (`PANEL_LIMITS`).
- **Five boxes.** The panel sits where the wearer looks
  ([risks](../live-capture.md#box-display-scope-change-adopted-2026-11-24)),
  the acceptance is about one object, and a wrong label is more visible as a
  box than as a name in a strip. Five leaves room for the object and a few
  neighbours and bounds the drawing.
- **Binary alpha and the project's own font.** A pixel is `(0, 0, 0, 0)` or
  has alpha 255; nothing is anti-aliased. Then the image means the same
  whether a compositor reads its alpha as straight or premultiplied (which
  is unverified on the Frame). Every drawn pixel is filled by the renderer
  itself: rectangles, and names from glyph bitmaps drawn by hand for this
  project (`box_font`; no font file and no third-party glyph data). No
  library draws a line or a letter, so the image does not depend on a
  rasteriser's version. (The first draft drew names with OpenCV's Hershey
  font, and OpenCV 4.12 and 4.13 rasterise it differently; review caught the
  digests failing on the Frame's Python 3.14 set.) The font is fixed-cell
  and coarse on purpose: 2-pixel strokes survive the headset's resampling
  better than 1-pixel ones.
- **Colours over a bright or a dark room.** No single colour contrasts with
  every background, so each mark carries its own contrast: the outline is a
  bright core between two dark lines, and the name is light on a dark plate.
  Core against casing is about 12:1 and name against plate about 16:1 (WCAG
  contrast ratio; a test requires at least 7:1). Over a white wall the dark
  casing is the visible edge, over a dark room the bright core is. The green
  is the strip's arrow colour.

### Bytes per update, a note for item B3

One update is 512 × 512 × 4 = **1,048,576 bytes** (`update_bytes`), 6.4 times
the strip's 163,840; with the 32-byte observed-frame prefix and at 5 updates
a second that is 5.2 MB/s through the pipe against 0.8 MB/s today. What that
implies, none of it built or measured here:

- The panel is exactly the largest frame the bridge accepts
  (`kMaxSide` × `kMaxSide` × 4), so the packet format and its limits can stay.
- A Linux pipe holds 65,536 bytes by default: one panel is 16 pipe-fulls
  where the strip is 2.5, so the client's writer blocks on the bridge's reads
  for longer. The in-band limit of the
  [stage](camera-hud.md#the-stage) (a hide is written behind at most the frame
  already being written) grows with it; B3 should measure the write and the
  upload on the fake runtime and decide whether to enlarge the pipe.
- `SetOverlayRaw` copies the whole megabyte each update. Its duration on the
  Frame is unknown, it sits inside the capture-to-display budget (400 ms p95
  proposed), and the third-party reports of a freeze after many raw updates
  ([risks](../live-capture.md#risks)) were not made at this size.
- The image arrives as RGBA already (`draw_boxes(...).tobytes()`); no colour
  conversion is needed. The bridge must present it with per-pixel alpha and
  the overlay's own opacity at 1.0. A compositor that filters the texture
  blends drawn pixels with transparent black at their border, which darkens
  the border; the outermost drawn pixel of every mark is already dark.
- The generated header does not carry the panel yet (below). When B3 adds
  it, `kContractDigest` changes and every bridge has to be rebuilt.

### The generated header

`tools/hud_contract.hpp` is **unchanged**: `sightcircuit/hud/native.py` does
not emit `BOX_PANEL_CONFIG`, so `scripts/hud_contract_header.py --check`
passes on the committed header, `kContractDigest` and `kHudConfigDigest` are
what they were, the bridge builds to the same program and no manifest or pin
moves. The panel's values reach the header in item B3, in the same change
that makes the bridge use them
(`_record("BoxPanel", contract.BOX_PANEL_CONFIG)` and its digest in
`_sections()`). `tests/test_hud_box_panel.py` pins the header's digest and
that it holds no box-panel name; B3 moves that one pin deliberately. The
strip's own pins (its fields, its configuration digest and its four tile
digests) are repeated in that test and must not move.

## The renderer

`draw_boxes(boxes, config)` returns a read-only `uint8` array of shape
(512, 512, 4), RGBA. Its input is at most `max_boxes` `PanelBox` values (a
sanitised name, a score, an integer rectangle `x0, y0, x1, y1` in
`box_panel_pixels` with the lower bounds inclusive and the upper exclusive)
and the configuration. It takes no frame and no image, and a test pins that
the module imports only the contract, the font and NumPy: no camera pixel can
reach the panel through it, and no text rasteriser is involved. More boxes
than the cap, or a box that `panel_box_valid` refuses, is a `ValueError`; the
renderer never drops silently. `layout(boxes, config)` returns the same
decisions as integer rectangles without drawing (each box's rectangle, open
sides, outline strips, the name as drawn, the plate and its placement); the
tests sweep it.

| Case | What is drawn |
| --- | --- |
| Not part of an outline or a name plate | fully transparent, RGB 0. The inside of a box is transparent |
| A box | a 4-pixel band inwards from its rectangle: dark, two bright, dark |
| A side of the box that lies on the panel's edge | nothing: the side is left open and its two neighbours run out to the edge, so a box cut there reads as continuing. Only the box's own side counts: a tiny box moved inwards (next row) keeps all its sides, also where it then touches the edge. A box that fills the panel has no side left and is its name alone |
| A box under 12 pixels wide or tall | 12 pixels on that axis about its own centre (an odd pixel goes right or down), moved back inside the panel when it would leave it |
| A name | light on a dark plate 24 pixels tall and as wide as the name plus 3 pixels each side |
| A name wider than 190 pixels (16 characters) | cut to its longest prefix that fits, without a trailing space and without a mark. Every COCO class name fits whole (the longest have 14 characters, 166 pixels) |
| Boxes that overlap | every outline is drawn before any plate, and within each pass the first box (the most confident, by `select_boxes`) is drawn last: it is on top, and no outline is drawn over a name |

### Where a name plate goes

A plate never covers any part of its own box's outline. The places are tried
in this order (`PLACEMENTS`):

1. `above`: on top of the box, from its upper left corner;
2. `below`: under the box, from its lower left corner;
3. `above_right`, 4. `below_right`: the same, ending at the box's right
   corners;
5. `right`, 6. `left`: beside the box, from its top;
7. `inside`: in the box's upper left corner, within the outline's band, the
   name cut further if the inside is narrower than the name.

A plate above or below is moved along the box only as far as the panel's
edge requires, and one beside it likewise. A place is possible when the
plate lies whole on the panel. Boxes are placed in their order, the most
important first, and each takes the first possible place that touches
neither a plate already placed nor the outline of any other box; failing
that, the first that touches no plate already placed; failing that, the first
possible. At least one place is always possible: a box too tall for a plate
above or below and too wide for one beside it has an inside that holds one
(the configuration's size rule guarantees it).

So a lone box always shows its whole outline, and at least 40 bright core
pixels of it (the smallest box in a corner of the panel, with two sides
open). Tests check this for every place offered to more than 80,000 small
boxes near the edges, corners and middle of the panel, for every position on
a small panel, and as pixels for a sample.

What can still collide: the outlines of two boxes cross where the boxes
overlap (the earlier box is on top); a plate may lie over the transparent
inside of another box; and when every place of a later box is taken, its
plate covers part of another box's outline or lies partly under an earlier
plate. With five boxes and seven places that needs a crowded panel.

Six reference scenes (empty, one box, boxes on the edges, tiny boxes, a
full panel with a long name, and all 65 characters) have pinned SHA-256
digests of their RGBA bytes. One digest per scene holds on both locked sets:
Python 3.12 with NumPy 2.2.6 and Python 3.14 with NumPy 2.3.2 (OpenCV is not
used). All are synthetic: names and rectangles written in the test.

## `camera_boxes`

`camera_boxes(detections, mapping, config, *, threshold, alignment=NEUTRAL)`
in `sightcircuit/hud/boxes.py` stands beside
[`camera_rows`](hud-runtime.md#row-selection) and returns a `BoxSelection`:
the `PanelBox` values for the renderer and a count of every detection that
got no box.

- `detections` are `Detection` values whose boxes are in
  `whole_frame_processing_pixels`, the one box space of the
  [region of view](../guides/camera-recognize.md#a-region-of-view) (item L14): what `Mapping.result`
  returns.
- `mapping` is the `region.Mapping` the frame was prepared with. It carries
  the region in use as the native crop actually taken, the rotation, and the
  size of the box space, so the region's rectangle is exact and not a
  rounded copy. The boxes themselves are not exact: see
  [resolution](#resolution).
- `threshold` is the caller's, as for `camera_rows` (`POC_THRESHOLD`, 0.5).
- At most 128 detections are accepted per frame.
- Numbers: any real number is taken, NumPy scalars of every width included,
  converted once to a plain `int` or `float`; a `bool` is not a number. This
  is the one place the two selectors differ: `camera_rows` takes `int`,
  `float` and their subclasses, so to it `np.float64` and `True` are numbers
  and `np.float32` and `np.int64` are not. No detector in the checkout emits
  either today.

The selection carries what it was made with: the configuration's digest, the
mapping's space, crop and rotation, the threshold and the alignment.

### The alignment

`Alignment(scale_x=1.0, scale_y=1.0, offset_x=0.0, offset_y=0.0, turn=0)` is
five numbers, frozen and checked when built:

| Number | Does | Range |
| --- | --- | --- |
| `scale_x`, `scale_y` | stretch the picture about the panel's centre, one axis each | 0.25 to 4 |
| `offset_x`, `offset_y` | move it, in panel widths and heights, positive right and down | −1 to 1 |
| `turn` | a further clockwise quarter turn about the centre, before the scale | 0, 90, 180 or 270 |

`+offset_y` increases the row index of the panel image. Whether row 0 is the
top of the panel as displayed is the bridge's to establish and test (B3).

`Alignment()` is neutral. The numbers are the project's own: the wearer sets
them in item B4, and they are format metadata that may be saved and reported
(the owner's answer 6 of 2026-11-24). They are not the SDK's calibration and
are never derived from it; the module reads no calibration value and a test
pins that it imports nothing that holds one.

### The mapping

Exact rational arithmetic (`fractions.Fraction`), rounded once at the end.
`panel_point(x, y, mapping, config, alignment)` is the unrounded map of one
point, for tests and for item B4.

1. **Region.** The region's rectangle in the box space is the native crop
   times `space / native_size`. A box whose centre is outside it is dropped
   (`outside_region`); a kept box is cut to it.
2. **Upright.** A point becomes fractions `(u, v)` of that rectangle and is
   turned clockwise by the mapping's rotation, in `region.Mapping`'s
   convention: at 90 the pixel `(x, y)` of a `w` × `h` image goes to
   `(h − y, x)`, so `(u, v)` goes to `(1 − v, u)`. The panel therefore shows
   the region as the detector saw it.
3. **Alignment.** The alignment's turn, then on each axis
   `0.5 + (p − 0.5) · scale + offset`.
4. **Panel.** Fractions times the panel's size. A box whose centre is off
   the panel is dropped (`off_panel`); a kept box is cut to the panel,
   rounded to whole pixels with halves up, and kept at least one pixel wide
   and tall.

With the neutral alignment the region's rectangle lands exactly on the
panel's rectangle, for every frame size, region and rotation. A box on the
region's edge therefore lies on the panel's edge and is drawn with that side
open, as a box cut by the panel is: in both cases the object may continue
where the picture ends.

The two drop rules are one rule used twice: a box is kept by its centre and
then cut. So a sliver of an object that is mostly out of the region or off
the panel is not shown as a small box at the edge.

### Resolution

A detection's box reaches `camera_boxes` as whole pixels of the box space:
`Mapping.to_frame` rounds it there, before this module. One frame pixel is
`512 · native / (crop · space)` panel pixels on an axis:

| Region, as a share of the frame's side | One frame pixel in panel pixels | Worst error of a box edge |
| --- | --- | --- |
| whole frame | 0.53 | 0.8 |
| half (the centre half) | 1.07 | 1.0 |
| a quarter | 2.13 | 1.6 |
| 5%, the smallest region allowed | 10.6 | 5.8 |

The worst error is half a frame pixel plus the half panel pixel of the final
rounding. A box edge stays within 2 panel pixels (a quarter of a degree)
while the region is at least 512 / (3 · 960) = 0.178 of the frame's side:
171 box-space pixels, 438 of the Frame's 2464. Below that the panel is
finer than the boxes it is given; a finer box space is a change to item
L14, not to this mapping.

### Selection

In this order, each detection counted under exactly one reason
(`DROP_REASONS`) when it gets no box:

| Reason | Rule |
| --- | --- |
| `invalid` | not a `Detection`, a box that is not four finite numbers, empty or outside the box space, a score that is not a number from 0 to 1, a label that is not text |
| `below_threshold` | score under `threshold` |
| `unnamed` | `row_label` leaves nothing printable: the vocabulary handling of `camera_rows` (ASCII letters, digits, space, `_` and `-`, at most 20 characters) |
| `outside_region`, `off_panel` | the mapping's two rules |
| `duplicate` | the same name and panel rectangle as a box ordered before it. Two objects of one name are two boxes |
| `over_cap` | beyond `max_boxes` |

The order is descending score, then name, then rectangle
(`contract.select_boxes`), so the cap keeps the most confident boxes and a
tie never depends on the detector's order.

### The report block

`boxes_report(selection, config)` returns the counts-only block for one
frame, with exactly the keys of `BOX_REPORT_KEYS`. The region, the threshold
and the alignment are the selection's own, the ones `camera_boxes` used, so a
caller cannot report other settings than were applied; a configuration other
than the one the selection was made for is refused
(`box_selection_config_changed`).

| Keys | Kind |
| --- | --- |
| `layout_id`, `config_sha256`, `panel_px`, `max_boxes` | the geometry, as format metadata |
| `box_coordinate_space` (`box_panel_pixels`), `source_coordinate_space` (`whole_frame_processing_pixels`), `source_space_px`, `region_crop_native_xyxy`, `region_rotation` | the two spaces and the region |
| `alignment` (the five numbers), `alignment_neutral`, `threshold` | the settings |
| `detections`, `boxes_shown`, `boxes_dropped`, `dropped` (a count per reason) | counts |

It holds no label, score, box or pixel. The camera stage's `check_report`
accepts it as a block (`blocks={"boxes": BOX_REPORT_KEYS}`); it is **not**
part of `CameraHudStage`'s report. Item B5 wires it in: per processed frame,
`region.prepare(frame, region)`, the detector, `mapping.result(result)`,
`camera_boxes(...)`, `draw_boxes(selection.boxes, config)`, and for the
report `boxes_report(selection, config)`, with the run's totals summed from each frame's
`dropped`.

## Cloud checks and what is unproven

`tests/test_hud_box_panel.py`: the strip's fields, digests, header and tiles
unchanged; the geometry's pins and its validation; that nothing outside the
four modules names the panel; the font (exactly the 65 characters, fixed
cells, distinct glyphs, pinned); the layout (open sides, tiny boxes, cut
names, the placement order, the sweeps above, collisions, random scenes);
the renderer's transparency, outline, glyph pixels, order, refusals and
digests. `tests/test_hud_camera_boxes.py`: the alignment's validation; the
identity and each alignment number over seven frame sizes (square and not),
six regions and four rotations; agreement with `region.Mapping` point by
point and through `Mapping.result`; the resolution table; the drop, cut and
rounding rules on the region's and the panel's edges; threshold, vocabulary,
NumPy numbers, order, cap and duplicates; the report block, what it carries
and its acceptance by the stage's schema; a generated frame through
`region.prepare` at each rotation. Both modules and
`tests/test_hud_camera_rows.py` pass on the Python 3.12 and the Python 3.14
locked sets.

Unproven, all of it device work that needs its own approval: that names at
7.5 pixels per degree (capitals 1.9°) are readable in the headset; that 0.75 m is a
comfortable distance for a box around a hand-held object; that 62° holds the
region L-D1b chooses; that the Frame's compositor honours per-pixel alpha
for a raw overlay; what a one-megabyte update costs at 5 a second; and row
G8, on which showing anything over the scene depends. Whether the
hand-drawn font reads well in the headset is likewise unseen.
