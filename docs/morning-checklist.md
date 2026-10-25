# Frame test checklist (after the overnight cloud work)

Run from the graphical session on Frame. Everything here is bounded; do not
activate capture layers or restart SteamVR. Commands are detailed in
[on-device](on-device.md). Record results as new [status](status.md) entries,
labelled device-confirmed, and keep cloud/CI results separate.

1. Update and set up: `git pull --ff-only` on this branch, `bash scripts/setup_window.sh`.
2. Refactor regression ([E49](status.md#e49-shared-pipeline-and-package-refactor-cloud-checks)):
   replay window with `--overlay` as in E47, then bounded mirror recognition as in E48.
   Expect the same behavior; note any difference.
3. New pacing: run replay with `--realtime --max-frames 200 --report <new file>`.
   Record `processed_fps`, `frames_dropped`, `mean_processing_ms`, `max_input_age_ms`.
   Check playback no longer looks slow-motion.
4. Detector comparison: capture stills with `--still` (include the rug and sofa),
   describe them in `evaluation/local/frame.json`, run `scripts/evaluate_detectors.py`.
   Choose the default detector from this, not the outdoor seed.
5. Window-in-mirror check (design-review next step 5): does the desktop window
   appear in the SteamVR mirror like the VR panel did (E18)?

Evidence template:

```
### E5x: <title>
Date / revision / SteamVR version:
Command:
Result (user-reported): ...
Passthrough/tracking normal during and after: yes/no
Not established: ...
```
