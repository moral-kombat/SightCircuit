# HUD observation and prediction tools

Use this reference for the marker observer, file-only scene analysis and geometric prediction. Observation and prediction support qualification; neither qualifies a mapping by itself.

[Architecture](../architecture.md) · [Qualification method](../hud-qualification.md) · [Current gates](../live-capture.md#edge-hud-acceptance-gates)

<a id="geometric-prediction-cross-check-2026-11-04"></a>

## Geometric prediction cross-check

Package M adds a geometry-only prediction of where the HUD quad should land in
the mirror's native pixels, to cross-check a G3 observation and the G4
composition contract. **A prediction cannot qualify a mapping.** It is
labelled `kind: geometric_prediction`, `prediction_only: true` and
`qualifies_mapping: false`; `sightcircuit.hud.qualification` and `candidate`
never read it, and a candidate whose evidence names `geometric_prediction` as
its kind is rejected (`hud_evidence_provenance_invalid`).

- `sightcircuit/hud/geometry.py` `project()` clips an eye-space polygon against
  a near plane (z = −0.02 m) and side planes widened by a quarter of the frame,
  then applies the pinhole given by the four `GetProjectionRaw` tangents.
  `hull()` is a monotone-chain convex hull. Both are ported from
  DeeJanuz/vr-recorder `vr-mask.cpp` L917-L977 (MIT; see
  [THIRD_PARTY](../../THIRD_PARTY.md#hud-geometric-prediction)). Upstream's comment
  says "no clamping", but its code clamps at four times the larger frame
  dimension (L949-L952). Here no point is clamped: after the side planes every
  point lies inside the widened window, so `project()` checks that and raises
  `hud_projection_outside_window` instead of bending the polygon.
- `predict_hud_footprint(view_record, config)` places the head-relative quad
  from `HudConfig` (identity rotation at `(x_m, y_m, -distance_m)`, height from
  the texture aspect), moves it into the selected eye with the inverse of
  `GetEyeToHeadTransform`, projects it and clips it to the view size. Its
  `status` is `inside`, `partly_off_frame`, `off_frame`, `behind_eye` (every
  corner at or behind the near plane) or `degenerate` (the projected hull has no
  area: an edge-on quad). A
  `both` view mode or a cropped view is reported as unsupported rather than
  guessed.
- Its assumptions are listed in the prediction: the mirror's native pixels are
  the IVRHeadsetView image of that eye at the view size, uncropped, with no lens
  or orientation change. Whether `/dev/video99` carries the IVRHeadsetView
  output is **unknown**, and the prediction records it under `unknowns`.

The view record comes from a read-only bridge mode:

```bash
artifacts/overlay-bridge/sightcircuit-overlay-bridge --headset-view-record --allow-runtime-query
```

It joins a running SteamVR as `VRApplication_Background` only (never an
Overlay connect, never a vrserver start) and prints `GetHeadsetViewMode`,
`GetHeadsetViewSize`, `GetHeadsetViewCropped`, `GetHeadsetViewAspectRatio`,
`GetHeadsetViewBlendRange`, and `GetEyeToHeadTransform` and
`GetProjectionRaw` for both eyes, in one JSON report under `view_record`. It
never calls `SetHeadsetViewMode` or any other `Set*` call; a test checks the
bridge source for that. The calls follow the read side of vr-recorder
`vr-mask.cpp` L1381-L1393. Without SteamVR it exits with one
`vrserver_not_running` error.

A saved record becomes a prediction with (owner decision, 2026-11-04):

```bash
python3 scripts/hud_view_prediction.py VIEW_RECORD.json
```

It accepts the whole bridge report or just its `view_record` object, reads
one regular file of at most 64 KiB, makes no OpenVR call, and prints a
`hud_view_prediction` report whose `prediction` is the labelled object above
with `mapping_qualified` false. Exit 0 for a prediction (whatever its
`status`), 1 for an unusable record.

Cloud checks: synthetic quads inside the frame, partly off it, behind the eye
and crossing the eye plane match hand-computed polygons, and the command is
driven on saved records (`tests/test_hud_prediction.py`); the view-record
mode's refusals are covered in `tests/test_overlay_bridge.py`. Reading the view record on Frame is
VR contact: it is person-run or Tier B, and it needs the owner's approval.

## Marker stop path

The next G3 attempt requires a changed marker-stop path with regression
coverage. That change was made on 2026-11-01 and is covered by cloud tests
only. The native marker mode ignores stdin. A requested stop, such as an
observer rejection, therefore closes stdin and waits for the bridge's own
display deadline plus two seconds. Until then the marker stays up, at most
`--seconds`. A V4-shaped frame-3 tag/bit rejection, run through the real
validation bridge, worker and guardian, now ends with clean software cleanup
and no forced termination. That run used generated input; it is not Frame
evidence. Ctrl-C, SIGTERM, a ready timeout or an overrun still escalates to
SIGTERM, then SIGKILL. That stop is reported as forced, with `cleanup_ok: false`.
Bridge reports add `stop_requested` and `escalated`. If the guardian is killed,
the supervisor now ends and reaps the worker group itself, and still reports
`cleanup_ok: false`. This does not explain the green input, the tag mismatch or
physical recovery. The [lifecycle review](../lifecycle-review.md#marker-stop-and-guardian-loss-2026-11-01)
has the details.

<a id="observer-scene-precondition-2026-11-04"></a>

## Observer scene precondition

**Recorded device result.** [E83](../evidence/e083.md) records two observer
runs on 2026-11-03 (`--seconds 20`, checkout `7c72a4e`). Both stopped in under
0.5 s with `observer_contour_budget`, before bridge initialization or marker
output. The first run counted 229 contours, then 2,680 with a screen in view;
the retry counted 2,720 while facing a wall that looked dim to the wearer.
Cleanup, passthrough and tracking were normal. The observer saved no images,
so the rejected pixels remain unknown. G3 stays open: neither run observed a
marker or established a mapping. Another attempt needs a reviewed precondition
change and new approval.

**The rule then.** Before looking for a marker, `locate_copies` thresholded
the grey frame at 180, traced every bright contour (`cv2.RETR_LIST`) and
rejected the frame above 2,048 contours. That count included single-pixel
specks, because the 25 px area filter for candidate quads came later. The
budget had been tuned on synthetic frames only. It also bounded nothing: it
was checked after `findContours` had already traced every contour. One
1080p lattice of 518,400 contours took 6 s to trace on the x86 cloud CPU.

**Statistics and still analysis (packages O1 and O2).** The precondition lives
in `sightcircuit/hud/scene.py`, and `locate_copies` calls it, so
`scene_statistics(image)` describes exactly what the observer applies.
[E84](../evidence/e084.md) records the merged implementation's cloud checks,
separately from E83's device result. It holds counts only, never pixels:

- the bright pixel count and fraction;
- grey p50, p90 and p99 (nearest rank);
- the contour count and its split into `cv2.contourArea` bands (below 25, 25
  to below 400, 400 to below 10,000, and 10,000 or more). The area is that of
  the polygon through the pixel centres, so a 5 × 5 blob has area 16;
- the candidate-quad and outline counts;
- the budgets, and the first limit, if any, that rejects the frame.

`hud_marker_observer.py --analyze-still STILL` prints those statistics for one
saved PNG or JPEG still.

- It never discovers or opens the mirror, a camera or the bridge.
- It refuses `/dev`, `/proc` and `/sys` paths, symbolic links, FIFOs,
  directories and other non-regular files before opening them. The file it
  then opens must be the same regular file, or it is closed unread. Only a
  parent directory swapped by another process in between could redirect that
  open.
- It reads at most 64 MiB and checks the image size in the file header before
  decoding.
- A PNG written by `mirror_recognize.py --still` claims to be the native mirror
  frame without loss (`fidelity: lossless_png_origin_unverified`). Its
  statistics equal the observer's on that frame only if it is one; the report
  says `provenance_verified: false` until its SHA-256 is matched to a capture
  report ([procedure](../guides/hud-checkpoint.md#measure-the-observers-scene-precondition-on-saved-stills)).
  A JPEG only approximates a mirror read.

A run stopped by a precondition check reports that frame's
`scene_statistics` once, with `scene_statistics_source`: the `baseline` or
`marker` phase and the read's sequence number. The worker report contract
accepts them only in a rejected report whose reason is the rejection they
name, and never with a rejection of none. There are two exceptions, and in
both the run is still rejected. If the statistics themselves fail, the match
diagnostics name the error in `scene_statistics_error`. If the traced contour
count disagrees with the census, the run is rejected as
`observer_contour_census_mismatch` with no statistics; that cannot happen while
the census is exact.

### The O3 rule (2026-11-06)

O3 replaces the raw count with two limits on marker-detection risk and three
compute bounds. The checks run in this order; the first that fails names the
rejection, and the statistics name the same one:

1. `observer_bright_saturation`: more than half of the pixels are at grey 180
   or above.
2. `observer_speck_density`: more than 1,700 specks per megapixel. A speck is
   a bright 8-connected component whose bounding box fits in 5 × 5 px.
3. `observer_contour_budget`: more than 4,096 bright contours. They are counted
   before any is traced, from connected components: bright 8-connected
   components, plus dark 4-connected components that do not touch the frame
   edge. That sum is exactly the number `findContours` returns, as a property
   test on random masks checks. After tracing, the traced count must equal
   the census, or the frame is rejected as `observer_contour_census_mismatch`,
   which backs the census against a change in OpenCV.
4. `observer_quad_budget`: more than 256 candidate quads. The scan stops at the
   257th.
5. `observer_outline_budget`: more than 16 outline-sized quads (shortest edge
   at least 32 px, longest at least 80 px, the matcher's own test). This is
   checked before any quad is warped.

The matcher after these checks is unchanged.

| Constant | Value | Derivation |
| --- | --- | --- |
| `MAX_BRIGHT_FRACTION` | 1/2 | A copy placed at random touches bright structure with probability at least the bright fraction. Above 1/2, a copy is therefore more likely than not to touch it, however the bright area is laid out. Touching is exposure, not certain loss, but it is usually loss. On composites, a white surround, a bright half-plane, a corner block or a thin line reaching the copy defeated detection at every scale from 0.75 to 3. A bar on its top edge did up to scale 1.5, but not at scale 2 and above. At scale 1 and above every such loss was a fail-closed orphan rejection; at 0.75, 1 to 5 of 10 per case were silent misses. |
| `SPECK_EXTENT_PX` | 5 | Any bright component inside a 5 × 5 box has a `contourArea` of at most 16, below the 25 px quad filter. So a speck can never be a candidate quad; it matters only where it lands on a copy. |
| `MAX_SPECKS_PER_MEGAPIXEL` | 1,700 | Suppose specks fall at random with density λ per pixel. The chance that none lands in a copy's harm area A is e^(−λA), which is at least 1/2 while λ ≤ ln 2 / A. Five runs composited a 5 × 5 speck on a copy at scale 0.75 (240 px wide, the low end of the assumed range). They gave A = 279.6 ± 44.2 px² (1,500 draws), 280.2 ± 27.9 (12,000 draws on background 40), 342.7 ± 21.1 (8,000 draws at 64 sub-pixel phases), 353.1 ± 31.3 (12,000 draws on background 25, the median grey of the rug and sofa stills) and 378.1 ± 25.6 (6,000 draws). The runs disagree by more than their errors, so A takes the largest plus one standard error, 403.7 px². Then ln 2 / A = 1,717 per megapixel, rounded down to 1,700. At scale 1 a replication gave 315 ± 38 px². In the 64-phase run, 167 of the harmed draws were fail-closed orphan rejections and 88 were silent misses. No run had a silent miss at scale 1 or above. So this limit mostly trades availability, not safety. The specks here are scene content under the overlay; noise drawn over the copy is covered below. The limit counts every speck as 5 × 5 (1 × 1 and 2 × 2 specks gave 29 and 134 px²), which is the cautious side. |
| `MAX_CONTOURS` | 4,096 | A compute bound, not a risk measure. It is twice the old budget, and the power of two just below the geometric mean (4,158) of the largest relayed Frame still (2,960 contours) and the 600 × 1000 noise grid (5,841). The census before tracing never traces. At 1080p on the x86 cloud CPU it took about 20 ms on a random 30 % mask and 65 to 105 ms on the 518,400-speck lattice; its cost grows with the number of bright components. Tracing costs about 10 µs per contour. |
| `MAX_QUADS` | 256 | Unchanged. A copy has 6 quads in the antialiased composites (scales 1 and 1.5), so 4 copies have 24. When a copy is scaled exactly 3 times by pixel replication, every one of its 17 contours is a quad, so 4 copies have 68. |
| `MAX_OUTLINES` | 16 | Each copy gives 2 outline-sized quads: its outer edge and the inner edge of its 4 px border. 4 copies (`MAX_COPIES`) give 8, and the budget doubles that. This was measured at scales 1 and 1.5 (`tests/test_observer_scene.py`), and also holds at scale 3. Each outline costs 8 perspective warps of the 320 × 128 template (2 corner orders × 4 rotations), or 16 when bounded refinement runs, each followed by the pattern checks. |
| `STATISTICS_MAX_CONTOURS` | 16,384 | A reporting bound, 4 × `MAX_CONTOURS`. Above it the statistics skip tracing, so the bands and the quad and outline counts are null, and they report `contour_scan: skipped_over_statistics_limit`. |

The slowest frame measured that passes every limit took 248 ms to the end of the
matcher on the 2.1 GHz x86 cloud CPU: 4,056 contours, 256 quads and 16
outlines, with no specks. One read period is 200 ms. The Frame's speed is
unmeasured. A flat frame takes 2 ms, and 4,096 non-quad contours take 50 to
100 ms.

Statistics are schema 2. They add `bright_pixel_count`, a `census` of
`bright_components`, `specks` and `enclosed_holes` (bright components plus
holes equal `contour_count`), `contour_scan`, `outline_count`, the five flags
and the schema-2 budgets. Each schema has a frozen budget table
(`SCHEMA_BUDGETS`). A report's declared budgets must equal its schema's table,
and every flag is recomputed from them. A stored O2 report (schema 1, with
2,048 / 256 / 25) still validates, and a forged budget does not.

**Classification.** The relayed Frame stills give no dimensions; they are
assumed to be 1920 × 1080 native mirror reads. The limits do not depend on
that size, but the speck densities below do. The relayed data have no speck
counts, so the count of contours below 25 px is used as an upper bound: every
speck has area 16 or less. Margins are limit ÷ value.

| Frame | Outcome | Reason |
| --- | --- | --- |
| bottle-in (15 contours, 0.37 % bright, 0 quads) | pass | specks ≤ 5.3/Mpx (×320); contours ×273; bright ×135 |
| bottle-out (5, 0.32 %, 0) | pass | specks ≤ 1.9/Mpx (×881); contours ×819; bright ×156 |
| rug (736, 15.8 %, 0) | pass | specks ≤ 247/Mpx (×6.9); contours ×5.6; bright ×3.2 |
| sofa (1,028, 4.9 %, 0) | pass | specks ≤ 474/Mpx (×3.6); contours ×4.0; bright ×10 |
| chair (241, 10.7 %, 0) | pass | specks ≤ 73/Mpx (×23); contours ×17; bright ×4.7 |
| invalid bottle-in (2,562, 28.4 %, 1 quad) | pass, reaches the matcher | specks ≤ 1,144/Mpx (×1.49); contours ×1.60; bright ×1.76; 1 quad, so at most 1 outline |
| invalid bottle-out (2,874, 25.2 %, 3) | pass, reaches the matcher | specks ≤ 1,307/Mpx (×1.30); contours ×1.43; bright ×1.98; at most 3 outlines |
| invalid rug (2,960, 28.1 %, 1) | pass, reaches the matcher | specks ≤ 1,345/Mpx (×1.26); contours ×1.38; bright ×1.78 |
| E61 grey still (640 × 360, not a native read) | pass | specks ≤ 894/Mpx (×1.9); 212 contours; 0.57 % bright; 0 quads |
| E83 run 1, read 1 (229 contours) | count limits pass by bound; saturation, quads and outlines unknown | specks ≤ 110/Mpx (×15); contours ×17.9; no bright fraction or quads were recorded |
| E83 run 1, read 2 (2,680) | count limits pass by bound; the rest unknown | specks ≤ 1,292/Mpx (×1.32); contours ×1.53 |
| E83 run 2, read 1 (2,720) | count limits pass by bound; the rest unknown | specks ≤ 1,312/Mpx (×1.30); contours ×1.51 |

The three invalid stills come from an attempt whose prompts never reached the
wearer. What was in view is unknown, so nothing says they are safe scenes.
They pass because every count they have is inside every limit. The matcher's
own checks then apply to them: orphan tags, ambiguity, overlap and the copy
budget. At 960 × 540 the bounds would no longer show a pass of the speck
limit for them, for the sofa (bounded at 1,896/Mpx) or for the two E83 reads
above 2,600 contours; E83 run 1 read 1 would still be bounded at 442/Mpx.

On the public seed stills (outdoor JPEGs, not mirror reads;
`tests/test_observer_scene_seed.py`):

- dog (2,523 specks/Mpx), person (10,805) and giraffe (12,496) reject on speck
  density;
- kite is 75 % bright and rejects on saturation, the first check (its 2,060
  specks/Mpx are over the limit too);
- horses (389/Mpx) and eagle (445/Mpx) pass.

Every synthetic noise frame still rejects:

- the 600 × 1000 grid of 5,841 specks, at 9,735 per megapixel;
- `dots(1021, 2, 4)` and every denser variant. `dots(1020, 2, 4)` is exactly
  at the limit and passes.

Each limit is tested at its boundary on both sides.

**What O3 does not close.**

- *Where copies can still be missed silently.* Bright structure touching a
  copy is the mechanism behind most losses on the seed composites. At scale 1
  and above it ends in fail-closed orphan rejections. Below about scale 0.8 (a
  copy under 256 px wide) it can instead remove a copy without an orphan. In a
  multi-copy run, that is a silent short count. Scene counts cannot see this;
  only a matcher change can. Two follow-ups are recommended, outside O3:
  - accept a copy only if at least 3 of its 4 tags are also detected as
    fragments;
  - default `expected_copies` whenever more than one copy is expected.
- *The marker's size in the mirror.* The derivation assumes scale 0.75 to 3
  (240 to 960 px wide) and takes each constant at the low end, where it is
  strictest. The only Frame-derived geometry is V4's candidate quad, 277 × 110
  px (scale about 0.86), and it failed its tag checks. At about scale 0.7 (224
  px wide) and below, the matcher misses copies even on a clean frame (clean
  composites: 22 of 24 found at 0.7, 13 of 24 at 0.65, 1 or 2 of 12 at 0.6). A
  side-by-side stereo mirror would put copies there.
- *The data.* The composites are synthetic: opaque paste, a bilinear warp, no
  mirror resampling, tone mapping or compression. Specks drawn over the copy
  (noise added after compositing, which a rendered mirror should not have)
  give a harm area about 2.5 times larger, 860 ± 126 px² at scale 0.75, which
  by the same rule would put the speck limit near 700 per megapixel. Most of
  that harm was fail-closed (35 orphan rejections against 10 silent misses in
  1,500 draws). Every Frame and E83 entry above comes from relayed aggregates.
  Frame stills analyzed with `--analyze-still` would replace those bounds with
  exact schema-2 counts.
