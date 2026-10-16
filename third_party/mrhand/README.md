# MRHAND protocol reference

`handcam_proto.h` is copied without modification from
[spyjacob/steam-frame-hand-tracking](https://github.com/spyjacob/steam-frame-hand-tracking/blob/ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082/capture/handcam_proto.h),
commit `ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082`. Its upstream
[MIT notice](LICENSE) is included here.

Framejacker uses only the published luma shared-memory layout. This header does
not supply a camera SDK, install a compositor layer or validate the upstream
writer's memory ordering. The fixture producer is original Framejacker test
code and publishes explicitly synthetic frames with a writer-side barrier.
See [the active plan](../../docs/live-capture.md) for integration status.
