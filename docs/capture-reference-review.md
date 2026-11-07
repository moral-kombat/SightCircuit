# Video stream reference review and strategy

Historical source review from **2026-10-16**. It compared the existing SteamVR
mirror with an experimental Arcturus capture path. Read the
[complete review](history/2026-11-07-capture-reference-review.md) for the original
analysis and immutable source links.

The [camera reference](steam-frame-camera.md) maintains interface and research
facts. The [current work plan](live-capture.md#current-work-order) owns priorities;
the dedicated camera provider remains deferred.

## Recommendation

The original recommendation was to test the existing composited mirror first,
then evaluate dedicated capture separately. Mirror capture and recognition were
subsequently demonstrated on Frame ([E48](evidence/e048.md)). The
[original recommendation](history/2026-11-07-capture-reference-review.md#recommendation)
records what was known before that result.

## What the references actually provide

The [source comparison](history/2026-11-07-capture-reference-review.md#what-the-references-actually-provide)
separates camera-buffer access, source switching and file reconstruction.
For maintained source descriptions, start with the [camera reference](steam-frame-camera.md#what-the-available-sources-establish).

## How dedicated Arcturus capture works

The upstream prototype shares camera-buffer descriptors through a Vulkan layer,
imports them in a separate process and publishes downscaled grayscale frames.
The [original diagram and protocol analysis](history/2026-11-07-capture-reference-review.md#how-dedicated-arcturus-capture-works)
describe that upstream prototype; they do not establish a working SightCircuit
camera backend.

## Findings that affect our implementation

The enduring limits are uncertain camera identity, missing producer
synchronization, buffer lifetime and layout requirements, and unverified
capture-plus-inference performance. Read the
[pinned findings](history/2026-11-07-capture-reference-review.md#findings-that-affect-our-implementation)
for the exact upstream behaviors. SightCircuit's metadata-only activation
results are recorded separately in [E65](evidence/e065.md); image access remains blocked.

## Implementation order and acceptance checks

The [historical sequence](history/2026-11-07-capture-reference-review.md#implementation-order-and-acceptance-checks)
is retained for context. Follow the [current work plan](live-capture.md#current-work-order)
and [current gates](live-capture.md#edge-hud-acceptance-gates) for new work.
Do not repeat the unchanged public camera probe, which interrupted passthrough
([E3](evidence/e003.md)).
