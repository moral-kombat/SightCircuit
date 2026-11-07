# Glossary

Use this page when a guide introduces an unfamiliar term. The
[architecture overview](architecture.md) shows how the pieces connect;
[current work](live-capture.md) explains which ones remain unfinished.

## Project and display terms

| Term | Meaning in SightCircuit |
| --- | --- |
| Frame | Steam Frame, the headset. Lowercase **frame** means one image in a stream. |
| POC | Proof of concept: a small demonstration with explicit acceptance criteria. |
| HUD | Heads-up display: object names and approximate arrows in a fixed strip at the edge of the view. |
| Head-relative | Positioned relative to the wearer's view; it moves with their head. |
| World-locked | Positioned at a location in the scene. This is deferred. |
| Passthrough | The headset's view of the physical surroundings. |
| SteamVR mirror | A composited image available from SteamVR. It can contain SightCircuit's own displayed output. |
| Bounded run | A run with explicit duration, frame, or resource limits and a defined stop path. |
| Feedback exclusion | Keeping the HUD's text, arrows, and background out of the image sent to the detector. |
| Footprint and mask | The pixels occupied by the HUD, including all observed copies and a conservative margin; the mask removes them before resizing and inference. |
| Expiry | Removing stale displayed results using the original observation time, independently of inference or heartbeats. |
| Calibration / qualification | A calibration describes a candidate mapping; qualification requires evidence that the mapping meets the acceptance conditions. |
| Synthetic input | Generated test images. Passing these tests does not establish headset behavior. |

## How to read identifiers

| Identifier | Meaning | Authoritative home |
| --- | --- | --- |
| E1, E75, … | Evidence records. An ID can describe success, failure, a source review, or an offline check. ID order is not execution chronology. | [Evidence catalog](evidence/README.md) |
| G1–G6 | Acceptance gates: synthetic checks; lifecycle/recovery; mapping observation; mapping qualification; feedback/expiry; end-to-end acceptance. These are separate from the three software gates. | [Acceptance table](live-capture.md#edge-hud-acceptance-gates) |
| WP1, WP9, … | Work packages used to track parts of the POC. A numbered package is not necessarily unfinished. | [Scope](poc-scope.md#work-packages) and [current work](live-capture.md#current-work-order) |
| V4 | A historical marker-observation attempt that was rejected. Its software termination is explained; that does not establish physical recovery. | [Recovered evidence E75](evidence/e075.md) |

## Implementation terms

| Term | Meaning |
| --- | --- |
| `FrameSource`, `Detector`, `Sink` | Interfaces for acquiring images, recognizing objects, and consuming results. |
| Latest-only processing | Replace a waiting image with a newer one when inference is slower than acquisition. Recorded replay can instead process every frame. |
| Native resolution | The original captured image dimensions, before resizing for the detector. |
| Fail closed | Stop or refuse a path when its required conditions cannot be established. |
| Guardian / supervisor | Processes that bound worker execution and contain failures. Reaping a worker does not prove normal headset recovery. |
| Producer synchronization | An agreement that a camera image is ready and can be read without its producer reusing it. Metadata alone does not provide this. |
| CI | Continuous integration: automated checks on build runners. An ARM64 runner is not a Steam Frame device test. |

For exact field names, timebases, and protocols, follow the subsystem reference
linked from the relevant guide. This glossary explains concepts; it does not
replace those contracts.
