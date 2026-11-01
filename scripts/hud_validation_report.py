"""Check a proposed HUD mapping record offline; never enables device output.

A valid record exits 2 (unqualified). JSON fields and artifact references do
not establish hardware evidence. No capture, inference or OpenVR is started.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from framejacker.gates import claims  # noqa: E402
from framejacker.hud.qualification import MAX_RECORD_BYTES, MappingCandidate  # noqa: E402


def read_record(path):
    # Refuse known devices/FIFOs/symlinks before opening; recheck the opened
    # object so a changed path cannot become an unbounded or blocking read.
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("hud_regular_record_file_required")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("hud_regular_record_file_required")
        data = stream.read(MAX_RECORD_BYTES+1)
    if len(data) > MAX_RECORD_BYTES:
        raise ValueError("hud_input_record_budget_exceeded")
    try:
        return json.loads(data)
    except RecursionError as error:
        raise ValueError("hud_input_json_nesting_exceeded") from error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-candidate", type=Path, required=True,
                        help="Local mapping candidate JSON; no images or remote input")
    parser.add_argument("--current-context", type=Path, required=True,
                        help="Local declared current context JSON; must match exactly")
    parser.add_argument("--now-ns", type=int, required=True,
                        help="Current monotonic time in the record's declared timebase")
    args = parser.parse_args(argv)
    try:
        candidate = MappingCandidate.from_mapping(read_record(args.check_candidate))
        candidate.check_context(read_record(args.current_context), args.now_ns)
        report = candidate.report()
    except (OSError, ValueError, TypeError, OverflowError) as error:
        report = {"report_kind": "hud_mapping_schema_check", "schema_version": 1,
                  "status": "rejected", "exit_code": 1, "reason": str(error)[:512], **claims()}
    print(json.dumps(report, indent=2, sort_keys=True))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
