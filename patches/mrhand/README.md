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

Both reference binaries remain non-executable inspection artifacts. GPU import,
upstream shared-memory writer ordering, provider lifetime and configuration
restoration still require work before deployment. Follow the
[active capture plan](../../docs/live-capture.md#current-work-order) and
[device procedure](../../docs/on-device.md#test-the-transport-adaptation-without-capture).
