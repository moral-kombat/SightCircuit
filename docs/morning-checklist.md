# Frame test checklist (after the overnight cloud work)

Automated: `bash scripts/frame_session.sh` runs the steps below in order and
writes reports and a pasteable `summary.md` to `artifacts/frame-session/<time>/`.
Stages can be selected, for example
`bash scripts/frame_session.sh setup regression pacing`; `overlay` (needs the
headset on) and `window-mirror` (step 5) are opt-in. Variables: `DEVICE`,
`RECORDING`, `SKIP_UPDATE=1`, `YES=1` (skip prompts), `COUNTDOWN=15` (start a
no-typing capture after a countdown), and `SCENES="rug|present|absent;sofa||"`
to describe still-capture scenes without typing between them. For example:

```bash
SKIP_UPDATE=1 COUNTDOWN=15 STEP_TIMEOUT=90 \
  bash scripts/frame_session.sh mirror
```

`COUNTDOWN` automates timing only; aim the headset during the countdown and
review the generated preview and report afterward. Interactive observations
may remain unanswered in the summary. Setup, regression, pacing, mirror,
replay-overlay and window-mirror commands have been exercised remotely on
Frame. Overlay visibility and passthrough/tracking still need user confirmation;
the window-mirror attempt was inconclusive; labelled stills and detector
comparison remain pending. The manifest helper has a unit test and the shell
script syntax has been checked. Stage-to-step mapping: setup = 1,
regression + overlay = 2,
pacing = 3, stills + evaluate = 4, window-mirror = 5 (mirror = E48 recognition).

Manual equivalents follow. Run from the graphical session on Frame. Everything here is bounded; do not
activate capture layers or restart SteamVR. Commands are detailed in
[on-device](on-device.md). Record results as new [status](status.md) entries,
labelled device-confirmed, and keep cloud/CI results separate.

1. Update and set up: `git switch main`, `git pull --ff-only`,
   `bash scripts/setup_window.sh`.
2. Refactor regression ([E49](status.md#e49-shared-pipeline-and-package-refactor-cloud-checks)):
   replay window with `--overlay` as in E47, then bounded mirror recognition as in E48.
   Expect the same behavior; note any difference.
3. New pacing: run replay with `--realtime --max-frames 200 --report <new file>`,
   once with the default 2 decoder threads and once with `--decode-threads 4`.
   Record `realtime_ratio` (about 1.0 is real time), `processed_fps`, `decode_fps`,
   `catch_ups`, `mean_processing_ms`, and whether playback looks real-time
   rather than slow motion.
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
