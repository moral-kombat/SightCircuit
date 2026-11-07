"""Check a proposed HUD mapping record offline, or build one from an observer report; never enables device output.

A valid record exits 2 (unqualified). JSON fields and artifact references do
not establish hardware evidence. ``--from-observer`` turns a saved
``hud_marker_observer.py`` report into a candidate whose unknown terms stay
explicit (``sightcircuit.hud.candidate``). Its session tokens come only from
the tokens the observer recorded (``--record-session-tokens``); a report
without them, or with a token that was not observed, is rejected. No capture,
inference or OpenVR is started.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sightcircuit.gates import claims  # noqa: E402
from sightcircuit.hud.candidate import (  # noqa: E402
    MAX_OBSERVER_REPORT_BYTES, candidate_report, observer_candidate, rejection_report)
from sightcircuit.hud.qualification import MAX_RECORD_BYTES, MappingCandidate, parse_record  # noqa: E402
from sightcircuit.runtime.output import publish_new  # noqa: E402

CHECK_OPTIONS = ("current_context", "now_ns")
OBSERVER_OPTIONS = ("declared_context", "lifetime_seconds")


def read_bytes(path, limit=MAX_RECORD_BYTES):
    # Refuse known devices/FIFOs/symlinks before opening; recheck the opened
    # object so a changed path cannot become an unbounded or blocking read.
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("hud_regular_record_file_required")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("hud_regular_record_file_required")
        data = stream.read(limit+1)
    if len(data) > limit:
        raise ValueError("hud_input_record_budget_exceeded")
    return data


def read_record(path):
    return parse_record(read_bytes(path))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-candidate", type=Path,
                      help="Local mapping candidate JSON; no images or remote input")
    mode.add_argument("--from-observer", type=Path,
                      help="Saved hud_marker_observer.py report JSON; builds an unqualified candidate")
    parser.add_argument("--current-context", type=Path,
                        help="With --check-candidate: local declared current context JSON; must match exactly")
    parser.add_argument("--now-ns", type=int,
                        help="With --check-candidate: current monotonic time in the record's declared timebase")
    parser.add_argument("--declared-context", type=Path,
                        help="With --from-observer: local JSON of the non-token context fields "
                             "(source_id, native_format, crop_xyxy, orientation_degrees, capture_layout, "
                             "hud_config_sha256); its native format must equal the observer's capture format. "
                             "It must not carry a session token: those come only from the observer report")
    parser.add_argument("--lifetime-seconds", type=int, metavar="1-300",
                        help="With --from-observer: candidate lifetime, counted from the first marker sample")
    parser.add_argument("--candidate-output", type=Path,
                        help="With --from-observer: new file for the candidate record; never overwritten")
    args = parser.parse_args(argv)
    required, forbidden = ((CHECK_OPTIONS, OBSERVER_OPTIONS + ("candidate_output",)) if args.check_candidate
                           else (OBSERVER_OPTIONS, CHECK_OPTIONS))
    if any(getattr(args, name) is None for name in required) or any(getattr(args, name) is not None
                                                                    for name in forbidden):
        flag = "--check-candidate" if args.check_candidate else "--from-observer"
        parser.error(f"{flag} requires " + " and ".join("--" + name.replace("_", "-") for name in required) +
                     " and accepts no other mode's options")
    return args


def check_candidate(args):
    try:
        candidate = MappingCandidate.from_mapping(read_record(args.check_candidate))
        candidate.check_context(read_record(args.current_context), args.now_ns)
        return candidate.report()
    except (OSError, ValueError, TypeError, OverflowError) as error:
        return {"report_kind": "hud_mapping_schema_check", "schema_version": 1,
                "status": "rejected", "exit_code": 1, "reason": str(error)[:512], **claims()}


def from_observer(args):
    try:
        candidate = observer_candidate(read_bytes(args.from_observer, MAX_OBSERVER_REPORT_BYTES),
                                       artifact_id=args.from_observer.name,
                                       declared_context=read_record(args.declared_context),
                                       lifetime_seconds=args.lifetime_seconds)
        report = candidate_report(candidate)
        report["candidate_output"] = None
        if args.candidate_output:
            # Written in full beside the target, then linked: a failed write leaves nothing, and nothing is replaced.
            record = (json.dumps(report["candidate"], indent=2, sort_keys=True) + "\n").encode()
            publish_new(args.candidate_output, record, ".hud-candidate-", ".json")
            report["candidate_output"] = str(args.candidate_output)
        return report
    except (OSError, ValueError, TypeError, OverflowError) as error:
        return rejection_report(error)


def main(argv=None):
    args = parse_args(argv)
    report = check_candidate(args) if args.check_candidate else from_observer(args)
    print(json.dumps(report, indent=2, sort_keys=True))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
