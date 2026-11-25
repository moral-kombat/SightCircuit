# Box panel: contract, renderer, `camera_boxes` and the bridge's layout

Work-order items B1, B2 and B3 of
[lane B](../live-capture.md#lane-b-the-box-panel): the cloud-side pieces of
the head-fixed box panel (Level 1 of the
[box display scope change](../live-capture.md#box-display-scope-change-adopted-2026-11-24)).
A box and a name are drawn around each recognised object on a transparent
panel fixed in front of the head.

**Status: cloud-checked only, and selected by no stage or command.** These
are a contract, a pure renderer, a pure mapping and, since item B3, a second
fixed layout of the bridge's `--fixed-hud` mode, tested on generated boxes
against a fake runtime. Nothing in this repository that is permitted to
drive real output names the panel, so its callers can show it only to the
fake runtime of the tests and to the validation bridge, which displays
nothing. That is a property of the repository's callers and of the
procedure, not of the bridge binary, which takes the layout from whoever
launches it ([who may show it](#who-may-show-it)). The wearer's
alignment step is B4 and `camera_hud`'s layout is B5. Nothing here has
displayed anything on a headset, opens a gate or reads calibration data. The
strip (`fixed-edge-hud-v1`, [row selection](hud-runtime.md#row-selection)) is
unchanged, byte for byte, and stays the names-only fallback.

| Piece | Where |
| --- | --- |
| Geometry `head-fixed-box-panel-v1` (`BoxPanelConfig`, `BOX_PANEL_CONFIG`), `PanelBox`, `panel_box_valid`, `select_boxes` | `sightcircuit/hud/contract.py` |
| The name font: hand-drawn glyph bitmaps for the 65 characters a label can hold | `sightcircuit/hud/box_font.py` |
| Renderer `draw_boxes(boxes, config)` and its geometry `layout(boxes, config)` | `sightcircuit/hud/box_render.py` |
| `Alignment`, `camera_boxes`, `panel_point`, `boxes_report` | `sightcircuit/hud/boxes.py` |
| The bridge's fixed layouts (`FIXED_LAYOUTS`, `fixed_layout`), and the header's copy of the panel | `sightcircuit/hud/contract.py`, `sightcircuit/hud/native.py`, `tools/hud_contract.hpp`, `tools/overlay_frame.hpp` |
| The client's `layout` and the refusal of real output with the panel (`layout_output_permitted`) | `sightcircuit/observed_frames.py` |
| `orientation_scene`: five boxes named by where they are drawn | `sightcircuit/hud/box_scenes.py` |
| Tests | `tests/test_hud_box_panel.py`, `tests/test_hud_camera_boxes.py`, `tests/test_box_panel_bridge.py` |

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

### Bytes per update

One update is 512 × 512 × 4 = **1,048,576 bytes** (`update_bytes`), 6.4 times
the strip's 163,840 and exactly the largest frame the packet format carries
(`kMaxSide` × `kMaxSide` × 4), so the packet format and its limits did not
move. With the 12-byte packet header and the 32-byte observed-frame prefix
one update is 1,048,620 bytes on the pipe; at 5 updates a second that is
5.2 MB/s against the strip's 0.8 MB/s. What that costs on a cloud host is
measured [below](#the-megabyte-measured-in-the-cloud); what it costs on the
Frame is unknown.

### The generated header

Since item B3 `tools/hud_contract.hpp` carries the panel:
`sightcircuit/hud/native.py` emits every field of `BOX_PANEL_CONFIG` as
`kBoxPanel*` and its digest as `kBoxPanelConfigDigest`, in the change that
made the bridge use them. The bridge reads the name, the size, the placement
and the digest; the drawing fields are carried so that `kContractDigest`
pins the whole record. `kContractDigest` therefore changed (from
`70663bdc…6c98` to `8192f645…0531`), and **every bridge has to be rebuilt**:
a bridge built before B3 reports the old digest and rejects `--layout`. The
strip's block of the header, `kHudConfigDigest` (`82f3f630…f392`) and the
strip's four tile digests are what they were; `tests/test_hud_box_panel.py`
and `tests/test_box_panel_bridge.py` hold both sides.

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

`+offset_y` increases the row index of the panel image. The bridge hands
row 0 to the overlay call first; whether the Frame displays row 0 at the top
is unproven ([row direction and alpha](#row-direction-and-alpha)).

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

## The bridge and the panel (item B3)

The bridge's real-output mode has two fixed layouts, chosen at launch and
never afterwards: `--fixed-hud` alone is the strip, exactly as before, and
`--fixed-hud --layout head-fixed-box-panel-v1` is the panel
([overlay reference](overlay.md#fixed-layouts-work-order-item-b3-2026-11-24)).
With the panel named, the bridge:

| What | How |
| --- | --- |
| Places the overlay | once, at connect, from the contract: 0.9 m wide, 0.75 m ahead, centred on the head (`x`, `y` zero), overlay opacity 1.0. No packet can move it; a settings packet ends the run |
| Takes frames | observed frames (opcode 6) of exactly 512 × 512. Any other size ends the run with `fixed_hud_frame_size`, and the strip's own 320 × 128 with `frame_for_other_layout`, before anything is uploaded |
| Presents pixels | the frame's bytes go to `SetOverlayRaw` unchanged, four bytes a pixel, in the order `draw_boxes(...).tobytes()` wrote them: row 0 first, each pixel R, G, B, A. The bridge does not convert, premultiply, flip or copy them, and the overlay's own opacity is 1.0, so transparency is the image's alpha alone |
| Expires, acknowledges, hides | by the strip's code: the deadline rule, the hide at the deadline, `frame_presented` and `frame_refused`, the visibility requests, the final hide and the watchdogs do not read the layout. The stream loop and the lifecycle unit do not contain the word (a test pins it) |
| Reports | the `fixed_hud` block with the strip's fourteen keys and the panel's values: `config_id`, `config_digest`, `width`, `height`, the placement as applied (`panel_width_m`, `distance_m`, `x_m`, `y_m`, `opacity`) and the three refusals. Numbers, booleans and two closed names. The `ready` line carries `layout_id` |

`--validate-stdin --layout head-fixed-box-panel-v1` applies the same size
rule without a runtime and reports a `validation_layout` block (name,
digest, size; no placement, since validation places nothing).

**No digest of a panel image is reported.** The strip's reports carry no
digest of a displayed tile either: `config_digest` is the digest of the
geometry's fields (sizes, placement, colours, the font's glyphs), which are
this repository's own values and say nothing about any frame. The panel's
equivalent is the same kind of value, `config_digest(BOX_PANEL_CONFIG)`. The
bridge's lines for a frame hold its identity, its times and whether it was
presented; no line or report holds a pixel, a count of drawn pixels or a
hash of an image.

### Who may show it

`ObservedFrameClient(layout=BOX_PANEL_CONFIG)` launches the bridge with the
layout, requires the `ready` line to repeat it, refuses any frame that is
not 512 × 512 × 4 bytes before queueing it (`observed_frame_not_layout_size`)
and confirms cleanup only when the bridge's own report names the layout. A
client without `layout` is the strip's client, unchanged.

Real output with the panel is refused before anything is launched and before
any scope record is consulted: `check_start` first asks
`observed_frames.layout_output_permitted`, which lets the strip alone pass,
and refuses any other layout by name (`fixed_hud_layout_not_approved`). It
only narrows: a layout it lets pass still meets the closed `LIVE_HUD_OUTPUT`
gate and the one permission function, as before. The check is needed. The permission to
drive real output (`fixed_hud_output_permitted`, the scope record) was
written when there was one geometry: a record binds `fixed-edge-hud-v1` and
its digest but never compared them with what the client was about to show,
so a client naming the panel would have been let through on a strip's
record. That permission, the record, the manifest, `CameraHudStage` and
`camera_hud` are unchanged and do not know the panel; a test pins that none
of them names it and that no construction of the client in the package
passes a layout. Item B5, which makes the record and the manifest bind the
layout, is where `layout_output_permitted` may come to permit more.

The layout launched is the layout judged. `start` reads the client's
layout once, hands that value to `layout_output_permitted` (which judges the
layout object itself, by identity with the contract's) and builds the
command line from the same value with `launch_arguments`, a plain function.
No method or property of the client lies between the two, so a subclass
that answers "the strip" when asked, or that would put another layout on
the command line, changes nothing; a test tries both.

So, **through this repository's Python**, the panel reaches a runtime only
in tests that patch both permission functions and point the bridge at the
fake `vrclient.so`, and reaches the validation bridge freely. Neither
displays anything.

**The bridge binary itself has no gate for the layout, and never had one
for anything.** Once a Frame's bridge is rebuilt from this source,
`sightcircuit-overlay-bridge --fixed-hud --layout head-fixed-box-panel-v1`
typed in a shell there would present whatever observed frames are piped to
it, exactly as `--fixed-hud` and `--stdin` typed in a shell already would
with the strip or a replay frame. "Only the fake runtime and the validation
bridge" is a property of the callers in this repository and of the
procedure, not of the binary: the rebuild and every launch of the bridge on
the Frame happen inside approved device steps, and that procedural model is
what stands between the binary and a headset, for the panel as for
everything else it can show.

### The megabyte, measured in the cloud

**Cloud figures, not Frame figures.** One x86_64 cloud container (4 virtual
cores of an Intel Xeon at 2.1 GHz, Linux 6.18, g++ 13.3, Python 3.12), the
real bridge against the fake runtime, whose `SetOverlayRaw` returns without
copying the image (0.04 ms). They show that the bridge's and the client's
own handling of a megabyte is small; they say nothing about the Frame's
compositor.

| Measured, 100 frames at 5 a second | Strip (163,840 B) | Panel (1,048,576 B) |
| --- | --- | --- |
| Building the packet in Python (prefix and header joined to the image: two copies) | 0.08 ms median, 0.2 max | 0.29 ms median, 1.9 max |
| Writing it to the default 65,536-byte pipe | 0.44 ms median, 1.4 max | 1.49 ms median, 3.4 max |
| From the start of the write to `frame_presented` | 0.56 ms median, 0.9 p95, 1.5 max | 1.57 ms median, 2.5 p95, 3.5 max |
| A hide request written right behind a frame, to `hide_returned` | 0.78 ms median, 1.1 max | 2.10 ms median, 2.8 max |
| A tile's deadline to its hide returning, nothing arriving (40 runs, lives spread over one poll) | 0.5 to 19.8 ms, median 10.0 | 1.0 to 20.1 ms, median 11.1 |
| The same with the next frame one third arrived | 0.8 to 20.1 ms, median 10.3 | 1.2 to 20.1 ms, median 10.7 |
| Drawing the panel (`draw_boxes`, five boxes) and `tobytes()` | | 0.67 ms and 0.07 ms median |

What follows from them:

- **The deadline is acted on within one 20 ms poll, for both layouts.**
  The bridge re-checks the deadline each time its input wait returns: when
  bytes arrive, or after 20 ms without any. Two cases follow, and the bound
  covers both. *Nothing arriving, or a frame partly arrived and then held*
  (the two rows of the table, and a sender that stalls): the wait runs its
  full 20 ms, so a hide comes anywhere from 0 to 20 ms after its deadline,
  about 10 ms in the middle, depending only on where in a poll the deadline
  falls. *Bytes still flowing* (a megabyte actually mid-arrival): every
  piece wakes the wait and the deadline is re-checked before the next read,
  so the hide follows the deadline by about the gap between pieces; the
  review measured 0.1 to 2.2 ms, median about 1 ms, for both layouts. A
  frame of 16 pipe-fulls therefore gives the bridge more checks, not a
  longer wait. The lives of the measured tiles are spread over 200 to
  219.5 ms so that the held case samples the whole poll. (A first version of this table used a 200 ms life in every
  run, a whole number of polls, which always put the deadline just before a
  wake-up and reported "within 6 ms" with differences between the layouts;
  both were artefacts of the test's timing.)
  `test_a_megabyte_in_flight_cannot_hold_a_tile_past_its_deadline` asserts
  one poll plus this host's scheduling margin at three split points, each at
  four places in the poll, and that the latenesses differ.
- **While a frame is being written to the overlay nothing can be hidden**,
  for either layout: `SetOverlayRaw` is synchronous and the bridge is one
  thread. The rule is the strip's and so is its bound: both deadlines are
  re-read when the call returns, a late frame is never shown, and the 5 s
  step watchdog ends a call that does not return. The strip's slow-upload
  tests pass on the panel. How long the Frame takes to copy a megabyte is
  the unknown that this bound waits on.
- **A hide request behind a frame trails by that frame's read and upload.**
  This in-band limit of the [stage](camera-hud.md#the-stage) grows with the
  frame: 2.1 ms median here against the strip's 0.8 ms. The new limit is the
  megabyte's read (about 1.3 ms more on this host) plus the Frame's unknown
  upload time; the bridge's own deadline, not the request, is what bounds a
  stale panel.
- **The pipe is not enlarged.** A 1 MiB pipe (`F_SETPIPE_SZ`) brought the
  write from 1.49 to 0.92 ms and write-to-presented from 1.57 to 1.03 ms:
  half a millisecond in a 200 ms period. It cannot hold a whole update
  either (1,048,620 bytes against this host's unprivileged ceiling of
  1,048,576, `/proc/sys/fs/pipe-max-size`, the kernel's default), and a
  kernel may refuse the request (`EPERM` above that ceiling or over the
  per-user pipe page limit), which would leave two transport configurations
  to qualify on the Frame. The default pipe is the one configuration measured and tested. The
  question returns only if a Frame measurement shows the read of a megabyte
  to matter: with the default pipe the bridge takes one update in at least
  17 reads, each preceded by one poll of the runtime's event queues, and the
  cost of those polls on the Frame's runtime is not known.
- **Copies of the megabyte per update.** In Python three: `tobytes()`, then
  the prefix and the header each joined in front (`observed_frame_packet`).
  Into the kernel's pipe and out again: two. In the bridge none of its own:
  the payload is read into one buffer (allocated and zeroed per packet) and
  `SetOverlayRaw` is handed a pointer into it. What the runtime's client
  library and the compositor copy is theirs and unmeasured.
- **At 5 updates a second** every frame was presented and no update hid the
  tile (`test_five_updates_a_second_are_each_presented_and_the_tile_stays_up`).

### Row direction and alpha

The cloud establishes what the bridge hands over. On the fake runtime the
bytes `SetOverlayRaw` received are compared with the renderer's, and they
are identical: 1,048,576 bytes, width 512, height 512, 4 bytes a pixel, the
image's row 0 first, alpha 0 or 255 as drawn, colour 0 wherever alpha is 0,
and overlay opacity 1.0 set once.

It cannot establish what the Frame's runtime then does. **Unproven:**
whether row 0 is displayed at the top of the panel and column 0 at its left
(no test here or earlier was built to show it), and whether the Frame's
compositor honours per-pixel alpha for a raw overlay (every image this
bridge has been given so far, the test pattern, the marker and the strip's
tile, is opaque throughout).

`box_scenes.orientation_scene(BOX_PANEL_CONFIG)` is the scene for a later
synthetic displayed step to settle both. Its five boxes are named by where
they are drawn in the image, `top left`, `top right`, `bottom left`,
`bottom right` and `centre`, and the four corner boxes differ in size and
shape, so a flip or a turn is visible without reading: **the largest box,
wider than tall, is the image's top left.** Both halves of that rule are
needed. Of the seven ways a square picture can be flipped or turned by
quarter turns, six move the largest box out of the top left, and the
seventh, a flip about the diagonal, leaves it there but stands it taller
than wide (160 × 96 pixels becomes 96 × 160); a test checks all seven. If
the wearer sees the largest box at the top left, wider than tall, named
`top left` with names reading left to right, row 0 is the top; if the room is
visible between and inside the boxes, alpha is honoured. Its RGBA digest is
pinned (`b4219d8d…8d0e`). The scene is boxes written in the module, not
detections; showing it is device work that needs its own approval and is
not built.

## Cloud checks and what is unproven

`tests/test_hud_box_panel.py`: the strip's fields, configuration digest and
tiles unchanged; the generated header, which since B3 holds the panel under
its own names with a new contract digest while the strip's block and digest
are what they were; the geometry's pins and its validation; that only the
listed files name the panel itself and only the listed files use the layout
mechanism (two separate lists), and that the package re-exports neither; the
font (exactly the 65 characters, fixed
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

`tests/test_box_panel_bridge.py` (item B3): the strip's whole fake-runtime
suite (`tests/test_fixed_hud_runtime.py`, unedited) run a second time on the
panel, a megabyte per tile, which is the evidence that expiry, renewal,
refusals, slow and failing hides, stalled uploads, end of input, SIGTERM and
the input timeout are one code path for both layouts; a megabyte in flight
at a deadline, at several places in the bridge's poll; a writer that stalls
mid-panel within and beyond the panel's life; thirty panels back to back;
input ending mid-panel; a hide request behind a megabyte; five updates a second; the
bytes handed to the overlay call; the report block; the launch arguments and
the validation stream's size rule; the bridge retiring the overlay when its
launcher is killed; the client's layout, its refusals and a bridge killed
under it; the refusal of real output, also against a subclass that would
part the layout judged from the layout launched; a static scan for anything
else that could name a layout; and the orientation scene under all seven
flips and turns.

Unproven, all of it device work that needs its own approval: that names at
7.5 pixels per degree (capitals 1.9°) are readable in the headset; that 0.75 m is a
comfortable distance for a box around a hand-held object; that 62° holds the
region L-D1b chooses; that the Frame's compositor honours per-pixel alpha
for a raw overlay, and which way up it shows the image; what a one-megabyte
`SetOverlayRaw` costs on the Frame at 5 a second, inside the proposed 400 ms,
and whether the third-party reports of a freeze after many raw updates
([risks](../live-capture.md#risks)) apply at this size; whether a
compositor that filters the texture darkens the border of a mark by blending
it with transparent black (the outermost drawn pixel of every mark is
already dark); and row G8, on which showing anything over the scene depends.
The Frame's bridge predates the layout and has to be rebuilt inside an
approved device step before any of this. Whether the
hand-drawn font reads well in the headset is likewise unseen.
