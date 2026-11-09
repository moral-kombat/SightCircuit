"""Fake camera export streams: valid ones, and one deliberately invalid stream per contract rule.

``valid_stream`` writes a complete camera export v1 stream (``ready``, frames,
``end``) with deterministic pixels; ``INVALID`` maps a name to a builder of a
stream that violates exactly one rule and the reason ``ExportReader`` must
refuse it with. ``camera_recognize --validate`` runs the reader over all of
them without a helper, and ``tests/fake_camera_export.py`` is the executable
stand-in for ``sightcircuit-camera-helper --export-frames`` built on these
builders. Nothing here touches a camera; the pixels are a synthetic gradient.
"""
import json

from . import contract

WIDTH, HEIGHT, STRIDE = 64, 48, 200  # Small frames with eight padding bytes per row (stride > width x 3).
LIMITS = {"fps": 5, "max_seconds": 2, "max_frames": 6}
BUILD = {"contract_digest": None, "commit": "0000000000000000000000000000000000000000", "dirty": False,
         "sdk_release": {"source_revision": "fake", "agreement_version": "2026-11-08"}}
ACQUISITION_FORMAT = {"drm_fourcc": "0x3231564e", "modifier": "0x0500000000000001", "plane_count": 2,
                      "planes": [{"offset": 0, "stride": 2464, "size": 6071296},
                                 {"offset": 6071296, "stride": 2464, "size": 3035648}],
                      "pixel_format": "nv12_ubwc", "encoding": "bt709", "transfer": "srgb", "primaries": "bt709"}
DIGESTS = {"controls_start": "0" * 64, "offline_cal": "f" * 64}
EXPORT_RATE_MONOTONIC_START_NS = 1_000_000_000_000  # An arbitrary host clock origin for the headers.


def encode(header, payload=b"", *, magic=contract.MAGIC, version=contract.VERSION, header_bytes=None,
           payload_bytes=None, header_text=None):
    """One packet; the overrides write deliberately invalid framing (sizes that lie, a bad magic, raw header text)."""
    text = json.dumps(header, separators=(",", ":")).encode() if header_text is None else header_text
    prefix = contract.pack_prefix(len(text) if header_bytes is None else header_bytes,
                                  len(payload) if payload_bytes is None else payload_bytes, magic=magic, version=version)
    return prefix + text + payload


def ready_header(*, limits=LIMITS, cameras=(0, 1), pixel_path="cpu_linear", backend="fake", **overrides):
    header = {"kind": "ready", "protocol": contract.VERSION, "backend": backend, "pixel_path": pixel_path,
              "build": dict(BUILD), "delivered_format": {"fourcc": contract.FOURCC}, "cameras": list(cameras),
              "limits": dict(limits)}
    header.update(overrides)
    return header


def frame_header(sequence, *, width=WIDTH, height=HEIGHT, stride=STRIDE, camera_index=None, derivation="cpu_converted",
                 monotonic_ns=None, dropped=0, **overrides):
    index = (sequence - 1) % 2 if camera_index is None else camera_index
    monotonic = EXPORT_RATE_MONOTONIC_START_NS + sequence * 200_000_000 if monotonic_ns is None else monotonic_ns
    header = {"kind": "frame", "sequence": sequence, "frame_id": 1000 + sequence, "camera_index": index,
              "exposure_ns": monotonic - 3_000_000, "monotonic_ns": monotonic, "realtime_ns": 1_762_650_000_000_000_000 + sequence,
              "width": width, "height": height, "fourcc": contract.FOURCC, "stride_bytes": stride,
              "buffer_bytes": stride * height, "derivation": derivation,
              "acquisition_format": json.loads(json.dumps(ACQUISITION_FORMAT)), "dropped_since_last": dropped}
    header.update(overrides)
    return header


def end_header(exported, *, reason="max_frames", dropped=0, **overrides):
    header = {"kind": "end", "reason": reason,
              "counts": {"exported": exported, "dropped": dropped, "acquire_failures": 0, "release_failures": 0},
              "elapsed_ns": 1_234_567_890, "digests": dict(DIGESTS)}
    header.update(overrides)
    return header


def pixels(sequence, width=WIDTH, height=HEIGHT, stride=STRIDE):
    """Deterministic BGR3 rows with ``stride`` bytes each: a gradient shifted by the sequence."""
    row = bytearray(stride)
    for x in range(width):
        row[3 * x:3 * x + 3] = ((x + sequence) & 255, (2 * x) & 255, (255 - x) & 255)
    return bytes(row) * height


def frame_packet(sequence, **overrides):
    header = frame_header(sequence, **overrides)
    return encode(header, pixels(sequence, header["width"], header["height"], header["stride_bytes"]))


def valid_stream(frames=LIMITS["max_frames"], *, limits=LIMITS, cameras=(0, 1), pixel_path="cpu_linear",
                 reason="max_frames"):
    """A complete, valid stream of ``frames`` frames alternating the selected cameras."""
    derivation = contract.DERIVATIONS[pixel_path]
    parts = [encode(ready_header(limits=limits, cameras=cameras, pixel_path=pixel_path))]
    for sequence in range(1, frames + 1):
        parts.append(frame_packet(sequence, camera_index=cameras[(sequence - 1) % len(cameras)], derivation=derivation))
    parts.append(encode(end_header(frames, reason=reason)))
    return b"".join(parts)


def _stream(*packets):
    return b"".join(packets)


def _ready(**overrides):
    return encode(ready_header(**overrides))


def _without(header, name):
    return {key: value for key, value in header.items() if key != name}


def _with_acquisition(**overrides):
    acquisition = dict(ACQUISITION_FORMAT, **overrides)
    return frame_packet(1, camera_index=0, acquisition_format=acquisition)


def _oversize_header():
    return encode(ready_header(build={"pad": "x" * (contract.MAX_HEADER_BYTES + 8)}))


def _oversize_payload_prefix():
    # The prefix alone claims a payload beyond the bound; the reader must refuse before reading it.
    return _ready() + encode(frame_header(1, camera_index=0), b"", payload_bytes=contract.MAX_PAYLOAD_BYTES + 1)


# name -> (builder of the stream bytes, the reason the reader refuses it with). One rule each.
INVALID = {
    # Framing.
    "truncated_prefix": (lambda: _ready()[:10], "truncated_prefix"),
    "bad_magic": (lambda: encode(ready_header(), magic=b"FJO1"), "bad_magic"),
    "unsupported_version": (lambda: encode(ready_header(), version=2), "unsupported_version"),
    "header_empty": (lambda: encode(ready_header(), header_bytes=0), "header_empty"),
    "header_too_large_prefix": (lambda: encode(ready_header(), header_bytes=contract.MAX_HEADER_BYTES + 1),
                                "header_too_large"),
    "header_too_large": (_oversize_header, "header_too_large"),
    "payload_too_large": (_oversize_payload_prefix, "payload_too_large"),
    "truncated_header": (lambda: _ready()[:-5], "truncated_header"),
    "truncated_payload": (lambda: _ready() + frame_packet(1, camera_index=0)[:-1], "truncated_payload"),
    "header_not_utf8": (lambda: encode(None, header_text=b'{"kind": "\xff"}'), "header_not_utf8"),
    "header_not_json": (lambda: encode(None, header_text=b'{"kind": '), "header_not_json"),
    "header_nonfinite": (lambda: encode(None, header_text=b'{"kind": "ready", "protocol": NaN}'), "header_not_json"),
    "duplicate_header_key": (lambda: encode(None, header_text=b'{"kind": "ready", "kind": "ready"}'),
                             "duplicate_header_key"),
    "header_not_object": (lambda: encode(None, header_text=b'["ready"]'), "header_not_object"),
    "missing_kind": (lambda: encode({"protocol": 1}), "missing_kind"),
    "unknown_kind": (lambda: encode({"kind": "frames"}), "unknown_kind"),
    "kind_not_string": (lambda: encode({"kind": 1}), "unknown_kind"),
    # Order of kinds.
    "frame_before_ready": (lambda: frame_packet(1, camera_index=0), "ready_not_first"),
    "end_before_ready": (lambda: encode(end_header(0)), "ready_not_first"),
    "ready_twice": (lambda: _ready() + _ready(), "ready_twice"),
    "frame_after_end": (lambda: _ready() + encode(end_header(0)) + frame_packet(1, camera_index=0), "packet_after_end"),
    "end_twice": (lambda: _ready() + encode(end_header(0)) + encode(end_header(0)), "packet_after_end"),
    "empty_stream": (lambda: b"", "stream_ended_without_ready"),
    "no_end": (lambda: _ready() + frame_packet(1, camera_index=0), "stream_ended_without_end"),
    "ready_only": (lambda: _ready(), "stream_ended_without_end"),
    # Ready fields.
    "ready_missing_field": (lambda: encode(_without(ready_header(), "limits")), "missing_field:limits"),
    "ready_unexpected_field": (lambda: _ready(camera="left"), "unexpected_field:camera"),
    "ready_protocol": (lambda: _ready(protocol=2), "invalid_field:protocol"),
    "ready_protocol_bool": (lambda: _ready(protocol=True), "invalid_field:protocol"),
    "ready_backend": (lambda: _ready(backend="real"), "invalid_field:backend"),
    "ready_pixel_path": (lambda: _ready(pixel_path="cpu"), "invalid_field:pixel_path"),
    "ready_build_not_object": (lambda: _ready(build="digest"), "invalid_field:build"),
    "ready_build_nested_too_deep": (lambda: _ready(build={"a": {"b": {"c": 1}}}), "invalid_field:build"),
    "ready_delivered_format": (lambda: _ready(delivered_format={"fourcc": "RGB3"}), "invalid_field:delivered_format.fourcc"),
    "ready_delivered_format_extra": (lambda: _ready(delivered_format={"fourcc": "BGR3", "width": 1}),
                                     "unexpected_field:delivered_format.width"),
    "ready_cameras_empty": (lambda: _ready(cameras=()), "invalid_field:cameras"),
    "ready_cameras_index": (lambda: _ready(cameras=(2,)), "invalid_field:cameras[0]"),
    "ready_cameras_repeated": (lambda: _ready(cameras=(0, 0)), "invalid_field:cameras"),
    "ready_cameras_descending": (lambda: _ready(cameras=(1, 0)), "invalid_field:cameras"),
    "ready_limits_fps": (lambda: _ready(limits=dict(LIMITS, fps=11)), "invalid_field:limits.fps"),
    "ready_limits_seconds": (lambda: _ready(limits=dict(LIMITS, max_seconds=31)), "invalid_field:limits.max_seconds"),
    "ready_limits_frames": (lambda: _ready(limits=dict(LIMITS, max_frames=301)), "invalid_field:limits.max_frames"),
    "ready_limits_missing": (lambda: _ready(limits={"fps": 5, "max_seconds": 2}), "missing_field:limits.max_frames"),
    "ready_payload": (lambda: encode(ready_header(), b"x"), "ready_payload_not_empty"),
    "ready_limits_differ": (lambda: valid_stream(limits=dict(LIMITS, max_frames=5)), "limits_differ_from_requested"),
    # Frame fields.
    "frame_missing_field": (lambda: _ready() + encode(_without(frame_header(1, camera_index=0), "monotonic_ns"),
                                                      pixels(1)), "missing_field:monotonic_ns"),
    "frame_unexpected_field": (lambda: _ready() + encode(frame_header(1, camera_index=0, pose=[]), pixels(1)),
                               "unexpected_field:pose"),
    "frame_sequence_zero": (lambda: _ready() + frame_packet(0, camera_index=0), "invalid_field:sequence"),
    "frame_sequence_float": (lambda: _ready() + encode(dict(frame_header(1, camera_index=0), sequence=1.0), pixels(1)),
                             "invalid_field:sequence"),
    "frame_camera_index": (lambda: _ready() + frame_packet(1, camera_index=2), "invalid_field:camera_index"),
    "frame_monotonic_negative": (lambda: _ready() + frame_packet(1, camera_index=0, monotonic_ns=-1),
                                 "invalid_field:monotonic_ns"),
    "frame_width_zero": (lambda: _ready() + frame_packet(1, camera_index=0, width=0), "invalid_field:width"),
    "frame_height_over": (lambda: _ready() + frame_packet(1, camera_index=0, height=contract.MAX_SIDE + 1),
                          "invalid_field:height"),
    "frame_fourcc": (lambda: _ready() + frame_packet(1, camera_index=0, fourcc="RGB3"), "invalid_field:fourcc"),
    "frame_derivation": (lambda: _ready() + frame_packet(1, camera_index=0, derivation="identity"),
                         "invalid_field:derivation"),
    "frame_dropped_negative": (lambda: _ready() + frame_packet(1, camera_index=0, dropped=-1),
                               "invalid_field:dropped_since_last"),
    "frame_acquisition_fourcc": (lambda: _ready() + _with_acquisition(drm_fourcc="NV12"),
                                 "invalid_field:acquisition_format.drm_fourcc"),
    "frame_acquisition_modifier": (lambda: _ready() + _with_acquisition(modifier="0x1"),
                                   "invalid_field:acquisition_format.modifier"),
    "frame_acquisition_planes": (lambda: _ready() + _with_acquisition(planes=[]),
                                 "invalid_field:acquisition_format.planes"),
    "frame_acquisition_plane_field": (lambda: _ready() + _with_acquisition(planes=[{"offset": 0, "stride": 1}]),
                                      "missing_field:acquisition_format.planes[0].size"),
    "frame_acquisition_pixel_format": (lambda: _ready() + _with_acquisition(pixel_format="unknown"),
                                       "invalid_field:acquisition_format.pixel_format"),
    "frame_acquisition_encoding": (lambda: _ready() + _with_acquisition(encoding="BT.709"),
                                   "invalid_field:acquisition_format.encoding"),
    "frame_acquisition_transfer": (lambda: _ready() + _with_acquisition(transfer="unknown"),
                                   "invalid_field:acquisition_format.transfer"),
    "frame_acquisition_primaries": (lambda: _ready() + _with_acquisition(primaries="bt601"),
                                    "invalid_field:acquisition_format.primaries"),
    # Frame stream rules.
    "frame_stride_too_small": (lambda: _ready() + encode(frame_header(1, camera_index=0, stride_bytes=WIDTH * 3 - 1,
                                                                      buffer_bytes=(WIDTH * 3 - 1) * HEIGHT),
                                                         pixels(1, stride=WIDTH * 3 - 1)), "stride_too_small"),
    "frame_buffer_bytes": (lambda: _ready() + encode(frame_header(1, camera_index=0, buffer_bytes=STRIDE * HEIGHT + 1),
                                                     pixels(1) + b"x"), "buffer_bytes_mismatch"),
    "frame_payload_short": (lambda: _ready() + encode(frame_header(1, camera_index=0), pixels(1)[:-1]),
                            "payload_bytes_mismatch"),
    "frame_payload_long": (lambda: _ready() + encode(frame_header(1, camera_index=0), pixels(1) + b"x"),
                           "payload_bytes_mismatch"),
    "frame_sequence_starts_at_two": (lambda: _ready() + frame_packet(2, camera_index=0), "sequence_not_consecutive"),
    "frame_sequence_gap": (lambda: _ready() + frame_packet(1, camera_index=0) + frame_packet(3, camera_index=0),
                           "sequence_not_consecutive"),
    "frame_sequence_repeated": (lambda: _ready() + frame_packet(1, camera_index=0) + frame_packet(1, camera_index=0),
                                "sequence_not_consecutive"),
    "frame_beyond_max_frames": (lambda: _ready() + b"".join(frame_packet(sequence, camera_index=0)
                                                           for sequence in range(1, LIMITS["max_frames"] + 2)),
                                "sequence_beyond_max_frames"),
    "frame_camera_not_selected": (lambda: _ready(cameras=(0,)) + frame_packet(1, camera_index=1), "camera_not_selected"),
    "frame_derivation_mismatch": (lambda: _ready(pixel_path="gpu_convert") + frame_packet(1, camera_index=0),
                                  "derivation_mismatch"),
    # End fields and rules.
    "end_missing_field": (lambda: _ready() + encode(_without(end_header(0), "digests")), "missing_field:digests"),
    "end_unexpected_field": (lambda: _ready() + encode(end_header(0, exit_code=0)), "unexpected_field:exit_code"),
    "end_reason": (lambda: _ready() + encode(end_header(0, reason="done")), "invalid_field:reason"),
    "end_counts_negative": (lambda: _ready() + encode(end_header(0, counts={"exported": 0, "dropped": -1,
                                                                            "acquire_failures": 0, "release_failures": 0})),
                            "invalid_field:counts.dropped"),
    "end_digest_not_hex": (lambda: _ready() + encode(end_header(0, digests={"a": "zz"})), "invalid_field:digests.a"),
    "end_digests_too_many": (lambda: _ready() + encode(end_header(0, digests={f"d{i}": "0" * 64 for i in range(17)})),
                             "invalid_field:digests"),
    "end_payload": (lambda: _ready() + encode(end_header(0), b"x"), "end_payload_not_empty"),
    "end_exported_mismatch": (lambda: _ready() + frame_packet(1, camera_index=0) + encode(end_header(2)),
                              "exported_count_mismatch"),
}
