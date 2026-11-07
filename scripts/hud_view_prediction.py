"""Turn a saved headset view record into a geometric HUD footprint prediction (JSON).

Input is the JSON the bridge's read-only ``--headset-view-record`` mode printed
(the whole bridge report, or just its ``view_record`` object). Output is the
prediction from ``sightcircuit.hud.geometry.predict_hud_footprint`` for the
current ``HUD_CONFIG``, printed as one JSON document. It reads one regular file
and makes no OpenVR call.

A prediction cannot qualify a mapping (docs/hud-qualification.md): the output
is labelled ``prediction_only`` with ``qualifies_mapping`` false, the report
carries the closed hardware-mapping gate claim, and nothing in qualification
reads it. Exit 0 for a prediction (whatever its status), 1 for an
unusable record, 2 for a usage error (argparse).
"""
import argparse
import json
import os
from pathlib import Path
import stat
import sys

if not __package__:  # Run as a file: import this checkout's packages without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sightcircuit import gates  # noqa: E402
from sightcircuit.hud import HUD_CONFIG  # noqa: E402
from sightcircuit.hud.geometry import predict_hud_footprint  # noqa: E402
from sightcircuit.hud.qualification import config_digest, parse_record  # noqa: E402

MAX_RECORD_BYTES = 65536
REPORT_KIND = "hud_view_prediction"


def read_view_record(path):
    """The view record in a bounded regular file: the bridge report's ``view_record`` or the record itself."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("hud_view_record_not_regular_file")
        if info.st_size > MAX_RECORD_BYTES:
            raise ValueError("hud_view_record_too_large")
        data = os.read(descriptor, MAX_RECORD_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(data) > MAX_RECORD_BYTES:
        raise ValueError("hud_view_record_too_large")
    document = parse_record(data.decode("utf-8", errors="strict"))
    if type(document) is not dict:
        raise ValueError("hud_view_record_invalid")
    if document.get("report_kind") == "sightcircuit_overlay_bridge":
        document = document.get("view_record")
    if type(document) is not dict:
        raise ValueError("hud_view_record_missing")
    return document


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("record", type=Path, help="JSON from the bridge's --headset-view-record mode")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    report = {"report_kind": REPORT_KIND, "schema_version": 1, "record": str(args.record),
              "hud_config_sha256": config_digest(HUD_CONFIG), **gates.claims(gates.HARDWARE_MAPPING)}
    try:
        record = read_view_record(args.record)
        prediction = predict_hud_footprint(record, HUD_CONFIG)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        report.update(status="record_unusable", error=str(error) if isinstance(error, ValueError) else type(error).__name__,
                      exit_code=1)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1
    report.update(status="predicted", prediction=prediction, exit_code=0,
                  scope=prediction["scope"], view_record_scope=record.get("scope"))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
