# Shared-memory reader reference

This diagnostic is independent of the active HUD path. The commands below use
generated data and temporary files. The [retained device procedure](capture-device-checks.md#check-existing-luma-shared-memory)
has different prerequisites; a synthetic result never establishes a real
provider's synchronization or camera identity.

## Isolated luma reader and synthetic producer

The shared-memory diagnostic is independent of inference, Qt, OpenVR and Vulkan.
Build it with the existing Linux C++17 compiler on x86_64 or ARM64:

```bash
bash scripts/build_shm_reader.sh
python3 -m unittest discover -s tests -p test_shm_capture_check.py -v
```

No SDK download, package installation, capture-layer activation or producer
startup occurs during the build. Native tests skip if the binaries are absent;
the native CI job builds them first. With PyAV installed, the preview test
also decodes the actual PNG. The user reports all 14 tests passed on Frame ARM64
in 7.204 seconds; see [E23](../status.md#e23-reference-build-and-controlled-reader-on-frame).

For a manual generated-image check, run this producer in one terminal:

```bash
mkdir -p artifacts/shm-check
artifacts/shm-reader/sightcircuit-shm-fixture \
  --create artifacts/shm-check/new-synthetic.shm --seconds 10 --mode split
```

While it is running, use a second terminal in the same checkout:

```bash
python3 scripts/shm_capture_check.py --read \
  --source artifacts/shm-check/new-synthetic.shm --camera 1 --seconds 3
```

The fixture publishes two 64 × 48 grayscale slots with differing uniform
intensities and deliberately split writes. It prints explicit synthetic
ready/closed events, runs for a bounded duration and leaves its ordinary file
for inspection. Use a new filename for each run; existing files and the
upstream `/dev/shm/mrhand-pt` path are refused. It does not acquire camera
images. Other controlled modes exercise unfinished, old, stalled, truncated,
replaced and restarted publications. Both tools require explicit arguments;
running either native binary without arguments returns `not_run`/exit `3`.

The pinned [protocol header and notice](../../third_party/mrhand/README.md) define
a 96-byte native header with two 32-byte slots, followed by `cams` luma planes.
Only 1–2 slots and at most 16 MiB per plane are accepted. The consumer requires
lock-free aligned 64-bit atomic sequence access, a stable even sequence around
each bounded copy, valid monotonic observation time and an unchanged source.
It maps read-only and performs at most ten copy attempts per second.

The original fixture adds a full writer barrier after the odd marker and a
release-store of the final even sequence. The reader uses acquire loads and a
barrier before its final sequence check. These controlled x86_64 checks do not
validate the upstream writer on ARM64 or original GPU-image completion. That
writer needs review/correction before a dedicated-provider trial. The layout
also lacks a generation field; observable counter regressions are rejected,
but not every restart can be detected between samples.

The Python supervisor contains native mapping faults (including a truncation
race) and enforces a deadline plus bounded terminate/kill cleanup. Linux
parent-death signaling prevents a stranded native reader; core dumps are
disabled in that reader. Optional PNG output is encoded only after input
closure and published atomically to a new path (`runtime/output.py` `publish_new`). No pixels are saved by default.
See [the device procedure](capture-device-checks.md#check-existing-luma-shared-memory) for
existing-provider usage and [evidence](../status.md#e9-shared-memory-reader-cloud-checks)
for actual validation limits.
