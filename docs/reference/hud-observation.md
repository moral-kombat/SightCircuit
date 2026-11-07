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
python3.12 scripts/hud_view_prediction.py VIEW_RECORD.json
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

**Why.** Before it looks for a marker, `locate_copies` thresholds the grey
frame at 180, finds every bright contour (`cv2.RETR_LIST`) and rejects the
frame above `MAX_CONTOURS` = 2048. That count includes single-pixel specks:
the 25-pixel area filter for candidate quads comes after it. The budget was
tuned on synthetic frames before the real-mirror rejection recorded in E83.

**What this adds (packages O1 and O2; the precondition itself is unchanged).**
The precondition now lives in `sightcircuit/hud/scene.py` (`bright_contours`,
`candidate_quad`), which `locate_copies` calls, so its statistics describe
exactly what the observer applies. [E84](../evidence/e084.md) records the merged
implementation's cloud checks, separately from E83's device result:

- `scene_statistics(image)`: the contour count and its split into
  `cv2.contourArea` bands (below 25, 25 to below 400, 400 to below 10,000,
  10,000 and over; the area is the polygon through pixel centres, so a 5 × 5
  blob has area 16), the fraction of pixels at or above 180, grey p50, p90 and
  p99 (nearest rank), the complete candidate-quad count, the budgets, and which
  budget, if any, rejects the frame. It holds counts only, never pixels.
- O1, file only: `hud_marker_observer.py --analyze-still STILL` prints those
  statistics for one saved PNG or JPEG still. It never discovers or opens the
  mirror, a camera or the bridge. It refuses `/dev`, `/proc` and `/sys` paths,
  symbolic links, FIFOs, directories and other non-regular files before
  opening them; the file it then opens must be the same regular file, or it is
  closed unread (only a parent directory swapped by another process in between
  could redirect that open). It reads at most 64 MiB and checks the image size
  in the file header before decoding. A PNG written by `mirror_recognize.py --still` is the native mirror
  frame without loss, so its statistics equal the observer's on that frame; a
  JPEG only approximates one, and the report says which (`fidelity`).
- O2: a run stopped by `observer_contour_budget` or `observer_quad_budget`
  now reports that frame's `scene_statistics`, with
  `scene_statistics_source` (`baseline` or `marker` phase and the read's
  sequence); the same statistics also sit in that phase's match diagnostics.
  The worker report contract accepts them only in a rejected report whose
  reason is the rejection they name, with exactly the documented fields.

**First non-Frame reference.** On the six public seed stills (outdoor JPEG
photos, not mirror reads), three exceed the contour budget (3,468, 3,606 and
9,375 contours), while 89 to 99 % of every still's contours are below 25 px
area and no still has more than one candidate quad
(`tests/test_observer_scene_seed.py`). This suggests the raw count rejects
ordinary scenes on speck-sized contours that the marker search would discard
anyway. It is not Frame evidence, and it does not change the budget: O3 will
replace the raw count with a measure of marker-detection risk, derived from
these numbers, synthetic noise and the Frame statistics that O1 makes
possible. Until then `MAX_CONTOURS` stays 2048 and a speckled scene still
rejects.
