# Capture transport adaptation

`0001-bounded-descriptor-transport.patch` applies only to the capture files at
`spyjacob/steam-frame-hand-tracking` commit
`ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082`. The upstream
[MIT notice](../../third_party/mrhand/LICENSE) applies to its source/context.
Original helpers in `tools/capture_transport.hpp` and the synthetic fixture
use Framejacker's MIT license.

The optional `--transport-fixes` reference build applies this patch to a
disposable copy and adds the helper header. The clean pinned cache is retained.
The patch duplicates registry FDs under the lock, freezes the response generation,
adds bounded partial-message handling and transfers received FD ownership into
RAII objects. Its scope is descriptor transport, not the full capture provider.

`0002-owned-bounded-publication.patch` applies after that patch. The optional
`--publication-fixes` build adds the original publisher/control helpers and
replaces the upstream writer with exclusive file creation, identity checks,
an odd-marker barrier, monotonic publication and cleanup. It adds explicit
stream opt-in, duration/rate bounds and signals; image-dump mode is removed.
The separate synthetic fixture exercises the same helper, without camera calls.

Both capture binaries remain non-executable inspection artifacts. GPU import,
GPU resource cleanup/watchdog, registry refresh, layer lifetime and configuration
restoration still require work before deployment. A loop deadline does not
bound every driver call. Follow the
[active capture plan](../../docs/live-capture.md#current-work-order) and
[publication device procedure](../../docs/on-device.md#test-owned-publication-without-capture).
