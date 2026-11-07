# Capture transport adaptation

`0001-bounded-descriptor-transport.patch` applies only to the capture files at
`spyjacob/steam-frame-hand-tracking` commit
`ff6a2b29bc50c9497d66fd7ff07e00b61f9eb082`. The upstream
[MIT notice](../../third_party/mrhand/LICENSE) applies to its source/context.
Original helpers in `tools/capture_transport.hpp` and the synthetic fixture
use SightCircuit's MIT license.

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

`0003-validated-image-import.patch` applies after both earlier patches. The
optional `--import-fixes` mode adds the original `tools/capture_import.hpp` and
its synthetic function-pointer fixture. It validates the reviewed metadata,
modifier/memory-plane and external-image capabilities, memory requirements and
FD ownership. Imported images are move-only; failure and completed-work paths
destroy images before freeing memory. The shared submission helper checks fence
reset/submit/wait before permitting retirement. Unconfirmed completion defers
API destruction to process teardown. Command/host-memory status checks are also
added; none of this establishes XRService producer synchronization.

`0004-owned-gpu-resources.patch` applies after patches 0001–0003. The optional
`--resource-fixes` mode adds original `tools/capture_gpu_resources.hpp` and its
fake-dispatch fixture. It validates bootstrap capability/queue selection, owns
the instance/device/pool and private GPU resources, and retains the device for
imported images. Static probe command resources are replaced with per-device
owners. Partial construction unwinds resources; uncertain completion defers
remaining API destruction and poisons the state until process exit. Host memory
invalidation and a private allocation budget are enforced. Actual driver
execution is not tested by the fixture; no XRService producer fence is supplied.

`0005-provider-lifetime-and-provenance.patch` applies after patches 0001–0004.
The optional `--provider-fixes` mode replaces the old layer implementation with
the original SightCircuit registry/dispatch/server helpers and extends the worker
to a separate v2 endpoint/packet ABI. It captures exporter metadata and GPU/driver
UUIDs, checks generations around copies/publication, scopes handles by device,
owns callback/server lifetime and preserves foreign runtime files. Original
watchdog and configuration tools provide bounded worker execution and exact
conditional recovery; only generated peers/fake dispatch run in cloud tests.
This patch contains removed upstream layer text under the upstream MIT notice.

`0006-block-unleased-camera-reads.patch` applies after patches 0001–0005.
The current `--provider-fixes` mode applies all six patches. It replaces the
latest raw camera reader with a no-runtime access gate: explicit capture is
blocked because producer release/ready/return synchronization is unavailable.
The original SightCircuit provider helpers now use a v3 metadata request, reject
image-buffer requests, and retain no image FDs in production layer state.
The separate metadata inspector reads only bounded registry records and links
without Vulkan. The patch retains removed upstream/adapted source text under
the same MIT attribution. Earlier non-executable modes preserve the historical
inspection code; they are not live-capture alternatives.

Both capture binaries remain non-executable inspection artifacts. Actual GPU
image access, original producer synchronization/FOREIGN ownership, and physical
camera identity remain unvalidated. The watchdog and provider/recovery
prerequisites passed their earlier 28-test v2 milestone on Frame ARM64; the v3
build and its 24 generated-data provider/diagnostic tests also passed there.
The later device-creation gate enforces opt-in even after legacy loader fallback;
its initial checks and the systemd trial/recovery runner were cloud-tested.

Later metadata-only provider loading and restoration were reported separately
in [E65](../../docs/evidence/e065.md). The full registry remained unstable, and
those trials did not establish synchronized image ownership. The provider track
stays paused and live image access remains blocked. Follow the
[active capture plan](../../docs/live-capture.md#current-work-order) and
[provider development reference](../../docs/reference/capture-provider.md).
