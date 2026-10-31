---
description: Steam Frame service control and capture trial constraints
applyTo: 'scripts/*service*.py,scripts/*session*.py,scripts/live_pipeline.py,scripts/recording_growth_check.py,scripts/bounded_worker.py,scripts/overlay_client.py,scripts/capture_layer_trial.py,tools/overlay_bridge.cpp,tests/test_*service*.py,tests/test_*session*.py,tests/test_bounded_worker.py,tests/test_live_pipeline.py,tests/test_recording_growth_check.py,tests/test_overlay_cleanup.py,tests/test_display_expiry.py,docs/live-capture.md'
---

The Frame's shipped `gamescope-session.service` has `PartOf=steamvr.service`:
stopping SteamVR also ends the graphical session. The reconstructed E63
service-only rehearsal (execution date unknown) reported a canceled stop job
and a new graphical session/SteamVR invocation; independent SSH and systemd cleanup survived. Before another
service-control implementation, model the whole session lifecycle. SDDM's
autologin and `start-gamescope-session` are now confirmed as the new session's
launch path. Preserve the session-policy preflight guard and metadata
activation block; do not remove platform dependencies or repeat a bare stop/start
to collect more evidence. The separately prepared `session_rehearsal.py` uses
the reviewed SteamOS Manager `switch-to-game-mode` interface and observes the
whole session; preparation/check modes do not log out. The reconstructed E64
operator session test completed and normal headset appearance was reported; its execution date and original report are unknown.
Never treat a successful session
cycle as permission to stage a layer during an assumed stopped interval.
See [the completed checkpoint](../../docs/status.md#e64-platform-session-test-completed-and-headset-confirmed).

The new `metadata_session_trial.py` avoids that assumption: each trial keeps
an immutable library at its own private path, publishes an environment-gated
manifest, and enables only a future SteamVR service start. Cleanup removes
only its exact manifest/drop-in, reloads the manager, and uses a second vendor
session request to retire the provider. It never replaces/deletes a mapped
library or clears foreign configuration. The first operator run loaded the
provider and restored configuration/runtime, but inspection failed on
`provider_registry_changed`. The v2 check permits at most four clean registry
invalidations within 12 seconds, retaining every attempt and requiring the
same provider/session plus a full two-second stable result. Never accept a
changed snapshot as stable or retry transport, identity or cleanup failures.
Keep the original
`capture_layer_trial.py` activation block and all pixel-access gates intact.

V2 recovery and normal headset operation are now confirmed. Its eight candidate
records match across three snapshots, but full-registry checks still fail and
continuous candidate stability/image ownership are unproven. Do not repeat v2
unchanged; the saved comparison is complete. The current minimum LIVE POC
target was October 26, 2026 (America/New_York) and was not met. Follow
[the critical path](../../docs/live-capture.md#minimum-live-poc-and-october-26-target).
This guide grants no new device operations: no deployments, headset tests or
restarts, source switching, provider activation, private-image transfer or server
exposure. Follow the active user request for offline changes and PR delivery;
existing or consumed device approvals do not authorize another run.
Recorder experiments are paused; the later 120-second watcher returned
`no_new_recording` with clean shutdown. Do not add recorder/lifecycle diagnostics
or repeat recorder experiments. The approved offline direction is a fixed edge
HUD with full-footprint native-resolution masking before resizing/inference.
The synthetic-only implementation and final offline tests are recorded in
[E69](../../docs/status.md#e69-offline-edge-hud-and-native-mask-validation); live
HUD input and real output remain blocked. The marker harness and observer have
executable, separately gated calibration preparation. Recovered
[E75](../../docs/evidence/e075.md) records a rejected historical V4 Frame attempt
and explains its software termination; the green physical outcome is unresolved.
There is no accepted G3 observation, and a future clean observation stays
`observed_unqualified`. The proposed
[G4 method](../../docs/hud-qualification.md#proposed-g4-method) and offline candidate
schema cannot establish qualification. Keep the object name and approximate
view-relative arrow inside its reserved
strip; it is a blind region, not world-locked output. Calibration, head-motion
leakage and physical VR expiry remain unproven. Invalid mapping or unexpected
capture/config changes must fail closed. See the current
[acceptance gates](../../docs/live-capture.md#edge-hud-acceptance-gates); the next
device action requires separate approval of bounded calibration.
Preserve the synthetic-only source and native-validation-only output gates in
the new pipeline until input prerequisites and explicit live-test authorization
are satisfied. Never treat matching sampled metadata or file growth as a
producer completion/ownership guarantee.

Frame/Arcturus input is required; an external-camera fallback is not accepted.
The offline lifecycle audit adds bounded JSON supervision, catchable signal
handling, owned-group reaping and honest forced-termination reporting. Preserve
these protections. The independent Linux guardian now covers loss of the outer
supervisor while the guardian remains runnable; it does not certify real
display expiry/feedback exclusion. Candidate-scoped metadata is a reviewed
design only: preserve the current global invalidation and producer gates.

## Learnings

An inference-thread age check and settings heartbeats cannot expire stale
content while inference stalls: use the native validation process's absolute
monotonic per-frame deadline, which settings cannot renew, and keep the expiring
packet blocked for real output until separately qualified. Derive that deadline
from original frame observation plus maximum age (or earlier); inference,
retransmission and heartbeats must not renew content lifetime. A Python finally
block cannot survive SIGKILL; the guardian must independently own the worker
group and detect parent loss through a pipe whose writer is never inherited by
workers. Reaping after parent loss is containment evidence, not a clean API
shutdown report from the dead parent; a stopped/killed guardian or stuck kernel
I/O prevents an unconditional cleanup guarantee. See [E68](../../docs/status.md#e68-independent-expiry-and-supervisor-loss-containment)
for deterministic no-VR tests and the remaining limits.
