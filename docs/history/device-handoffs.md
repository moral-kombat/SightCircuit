# Earlier device handoffs and deployment notes

**Historical reference.** The material below preserves earlier proposed
experiments and session prompts. It contains obsolete next steps and status
claims; do not use it as a current execution prompt. Read the
[current plan](../live-capture.md), [status](../status.md), and the current
[device guide](../on-device.md) instead.

Evidence is now recorded in [individual records](../evidence/README.md);
`status.md` is generated and must not be edited by hand.

## Proposed recording growth test

**Paused.** This procedure is kept for reference; no new recorder experiment
is planned, and the active device work is the
[edge HUD calibration checkpoint](../guides/hud-checkpoint.md#bounded-edge-hud-calibration-checkpoint).
No Frame result for it is recorded in [status](../status.md).

The question it would answer is whether the vendor camera app publishes a
recording while it is still being written. `scripts/recording_growth_check.py`
reads only top-level MP4 box headers, never media payloads, and decodes no
images. It runs bounded and identity-checked in a supervised worker. With no
mode it does nothing (status `not_run`, exit 3). Its design and offline tests
are in [development](../development.md).

If it is approved again, start the watcher first, then start a **new**
recording in the vendor app within the wait window:

```bash
cd "$HOME/sightcircuit"
python3 scripts/recording_growth_check.py \
  --watch-directory "$HOME/Videos/ArcturusVisionCamera" \
  --wait-for-new 30 --seconds 15
```

The watcher ignores existing clips and waits up to `--wait-for-new` seconds
(at most 120) for exactly one new `*_stereo.mp4`; it refuses an ambiguous
choice. It then observes that file's headers for `--seconds` (at most 30).
`--observe FILE` observes one file the operator chose instead.

A positive result only shows that fragment extents kept growing while the file
was written. It does not prove a complete payload, a usable live source or
that the source excludes our VR panel. Keep the JSON report and record it as a
device-command result, separate from cloud tests.


## Deployment preflight and command shape (not authorized)

[Development](../reference/pipeline.md#offline-foundation-for-the-minimum-live-poc)
refers to a local eight-entry deployment manifest (the ARM64 bridge plus seven
Python modules) with verified sizes and hashes, and to a placeholder deployment
command. **Neither the manifest nor that command is in this repository.** This
section therefore gives no deployment command. No install, transfer or device
execution is authorized here. The [checkpoint artifact manifest](../hud-qualification.md#next-device-checkpoint)
is different: it pins a checkout and bridge built on Frame itself, and nothing
is transferred.

That historical file list is also insufficient for the current adapters:
shared code now lives only in `sightcircuit/` (for example the mirror reader in
`sightcircuit/sources/mirror_device.py` and the guardian in
`sightcircuit/runtime/guard.py`); `scripts/` holds entry points. A new manifest must include the complete
package/script dependency closure and matching source revision, plus the
target-built ARM64 bridge. This delivery's local x86_64 bridge is an offline
validation artifact, not a device deployment candidate.

Before any deployment is proposed, the preflight must establish at least:

- The exact commit and a manifest listing every deployed file with its size
  and SHA-256, compared on Frame before use.
- That cross-compiled or Windows-built artifacts are not taken as evidence of
  Frame runtime compatibility. The bridge used on Frame so far has been built
  on Frame with `bash scripts/build_overlay_bridge.sh`.
- Target ARM64 UAPI, `ctypes` and `ioctl` size execution evidence before any
  capture path runs. The cloud ABI test passes with its own compiler/UAPI;
  that does not establish the target headset's ABI.
- Independent SSH, a clean checkout, and nothing else running, as in the
  [calibration checkpoint](../guides/hud-checkpoint.md#bounded-edge-hud-calibration-checkpoint).

Commit the manifest and its preflight script, then replace this section with
the real command shape when that work is reviewed.


## Context for a new development session

```text
Continue moral-kombat/sightcircuit on this Steam Frame.
Read docs/README.md and docs/status.md first, then docs/on-device.md.
Confirm where this terminal executes before running device commands.
Use docs/vr-overlay.md to resume recorded replay and the head-relative panel.
The public camera probe returned no frames and interrupted passthrough.
Do not repeat it on the unchanged runtime; its explicit flag is for a deliberate
future retest after a material change.
Read docs/live-capture.md#current-work-order for the next engineering task.
The corrected mirror reader received 100 complete 1920x1080 RGB3 frames from
/dev/video99 in ten seconds with changing samples and normal cleanup.
An explicit 640x360 preview was saved at artifacts/mirror-check/scene-check.png.
The user recognized a black-and-white photo of the room via its absolute file URL.
The one-image detector ran, but a rug was mislabeled sofa; the demo has no rug class.
Bounded mirror recognition then processed 51/100 reads at 4.993 fps with normal
passthrough and clean shutdown. Earlier labels were chair/tvmonitor; the final
frame had no detections. The fixed-panel test then confirmed our panel appears
in mirror input; its grayscale/no-label output was expected because no detector ran.
Arcturus is connected; its selection for passthrough is unknown. The latest
prerequisite report is aarch64 SteamOS 0.5.6 build 20261015.6141207, GCC 15.1.1
and Vulkan 1.4.309 with development headers. Earlier capture tests have their
own runtime evidence; do not silently attach this newer build to them.
The pinned reference compiled/linked on aarch64 with system Vulkan headers; all
14 synthetic reader tests passed in 7.204 seconds. The descriptor transport
adaptation also passed seven fixture groups on ARM64. The publication build
completed and all eight generated-data tests passed in 1.562 seconds on Frame.
The bounded Vulkan query passed on Turnip Adreno 750: required extensions,
graphics family 0, cached host memory, R8 blits and dedicated DMA-BUF imports are
advertised, with two memory planes for the NV12 modifier. Loader API is 1.3.296;
device API is 1.4.362. Normal passthrough stayed working during and after it.
The validated-import inspection adaptation compiled on aarch64 and passed all
ten original synthetic tests in 0.015 seconds. A new two-plane fixture test now
passes in the cloud; that eleventh test has not run on Frame. Actual camera
imports remain unverified. Both original device checks are complete. The v2
watchdog/provider/recovery milestone also passed all 28 tests on Frame ARM64.
Producer synchronization review found no coordinated handoff, so the latest v3
provider records metadata without retaining/exporting image FDs and blocks live
reads. The earlier v3 ARM64 build and both suites (12 provider/layer tests and
12 metadata-diagnostic tests) passed. The inactive bundle and active SteamVR
service properties were received. A bounded metadata-only trial with independent
systemd post-exit recovery was prepared for a trial. The received 391127a report
shows a passing dummy recovery rehearsal, followed by worker and rollback
systemctl errors. Configuration restoration is reported; SteamVR was later
active/no drop-ins, with working UI/passthrough. No inspection report was produced
and actual layer loading is unverified. Activation is blocked while a focused
read-only service query diagnoses the service failure. The focused journal
shows stopping at 10:36:41, worker failure at :42, stopped/rollback error at :46,
and startup at :47–48. Manual start/stop are allowed; Restart=always and a zero
restart counter do not establish what initiated startup. Next read service
relationships with --service-details. Do not repeat the trial or rollback on
the working headset. Current trial tests number 24 in cloud.
Physical camera identity and scene freshness remain unverified.
GPU device/resource ownership is implemented in --resource-fixes and has nine
synthetic cloud tests passed, including 57 injected startup/resource errors.
Its native ARM64 and actual driver execution remain untested; real capture
binaries are still non-executable inspection artifacts.
The isolated shared-memory reader and synthetic fixture are implemented and
tested on cloud x86_64 and Frame ARM64; real-provider validation and capture-layer
deployment are pending.
Since 2026-10-25 the replay and mirror commands run on the shared sightcircuit
pipeline (cloud and CI only so far; reconfirm replay/overlay and mirror recognition
on Frame). Re-run scripts/setup_window.sh, then compare detectors on Frame stills:
docs/on-device.md#compare-detectors-on-frame-stills. Next steps are in
docs/design-review.md#next-steps.
Keep user observations, cloud checks and upstream capture claims distinct.
Record new results in docs/status.md, including unknown dates/versions explicitly.
```

[valve-setup]: https://partner.steamgames.com/doc/steamhardware/steamframe/setup
[frame-ssh]: https://github.com/thunderheronight/splat-workshop/blob/a4ef32f28aab0b8372c09a8c221ef0f59bfeff4a/README.md#ssh-access-to-the-frame
[arcturus-camera]: https://store.steampowered.com/app/4103250
[arcturus-howto]: https://arcturus.vision/howto
[recording-folder]: https://github.com/thunderheronight/splat-workshop/blob/a4ef32f28aab0b8372c09a8c221ef0f59bfeff4a/scripts/splat.sh#L35

[valve-setup]: https://partner.steamgames.com/doc/steamhardware/steamframe/setup
[frame-ssh]: https://github.com/thunderheronight/splat-workshop/blob/a4ef32f28aab0b8372c09a8c221ef0f59bfeff4a/README.md#ssh-access-to-the-frame
[arcturus-camera]: https://store.steampowered.com/app/4103250
[arcturus-howto]: https://arcturus.vision/howto
[recording-folder]: https://github.com/thunderheronight/splat-workshop/blob/a4ef32f28aab0b8372c09a8c221ef0f59bfeff4a/scripts/splat.sh#L35
