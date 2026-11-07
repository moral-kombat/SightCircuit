"""Bounded MP4 growth observation: top-level headers only, never media payloads.

The header scan (``scan_headers``) lives in ``sightcircuit.sources.recording``; this
command watches one growing file and reports what changed.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import sys
import threading
import time

if not __package__:  # Run as a file: import this checkout's packages without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sightcircuit.runtime.guard import stop_on_signals, supervise  # noqa: E402
from sightcircuit.sources.recording import Budget, GrowthError, scan_headers  # noqa: E402

MAX_WAIT_SECONDS = 120


def identity(info):
    return info.st_dev, info.st_ino


def new_report():
    return {"report_kind": "recording_growth_headers", "schema_version": 1,
        "status": "not_run", "exit_code": 3, "media_payload_read": False,
        "images_decoded": 0, "image_access_enabled": False, "live_source_verified": False,
        "payload_publication_verified": False, "feedback_exclusion_verified": False,
        "cleanup_ok": True, "observations": 0, "growth_events": [],
        "scope": "Top-level MP4 headers and file identity only; no frame, writer-publication or live-input proof"}


def recording_names(directory):
    if not stat.S_ISDIR(directory.lstat().st_mode):
        raise GrowthError("watch_directory_must_not_be_a_symlink")
    names = set()
    with os.scandir(directory) as entries:
        for count, entry in enumerate(entries, 1):
            if count > 512:
                raise GrowthError("recording_directory_entry_limit")
            if entry.name.endswith("_stereo.mp4") and entry.is_file(follow_symlinks=False):
                names.add(entry.name)
    return names


def watch_new(directory, wait_seconds=15, seconds=15, stop=None, *, clock=time.monotonic,
              pause=time.sleep, announce=False):
    if not 0 < wait_seconds <= MAX_WAIT_SECONDS or not 0 < seconds <= 30:
        raise GrowthError("new_recording_wait_out_of_bounds")
    stop = stop or threading.Event()
    report = new_report()
    if stop.is_set():
        return dict(report, status="cancelled", exit_code=130)
    try:
        directory = Path(directory).expanduser().absolute()
        known = recording_names(directory)
        if announce:
            print("Watching for a new stereo recording. Start recording now and keep it running until this check ends.",
                  file=sys.stderr, flush=True)
        started = clock()
        deadline = started + wait_seconds
        while clock() < deadline and not stop.is_set():
            new = recording_names(directory) - known
            if stop.is_set() or clock() >= deadline:
                break
            if len(new) > 1:
                raise GrowthError("multiple_new_recordings_require_explicit_selection")
            if new:
                name = next(iter(new))
                waited = clock() - started
                report = observe(directory / name, seconds, stop, clock=clock, pause=pause)
                report.update(selection="new_file_after_watch_started", selected_name=name,
                              waited_seconds=round(waited, 3))
                return report
            pause(min(0.2, max(0, deadline - clock())))
        report.update(status="cancelled" if stop.is_set() else "no_new_recording",
                      exit_code=130 if stop.is_set() else 2)
    except (OSError, ValueError) as error:
        report.update(status="error", exit_code=1, reason=str(error)[:1024])
    return report


def observe(path, seconds=15, stop=None, *, clock=time.monotonic, pause=time.sleep, budget=None):
    if not 0 < seconds <= 30:
        raise GrowthError("duration_out_of_bounds")
    stop, budget = stop or threading.Event(), budget or Budget()
    report, fd, stream = new_report(), None, None
    if stop.is_set():
        return dict(report, status="cancelled", exit_code=130)
    report.update(status="error", exit_code=1, seconds_requested=seconds)
    try:
        path = Path(path).expanduser().absolute()
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise GrowthError("input_must_be_regular_file_without_symlink")
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags)
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or identity(opened) != identity(before):
            raise GrowthError("file_identity_changed_on_open")
        started = clock()
        deadline = started + seconds
        old_headers, old_pairs, previous_size = [], set(), opened.st_size
        initial_pairs = None
        stream = os.fdopen(fd, "rb", buffering=0)
        fd = None
        while clock() < deadline and not stop.is_set():
            current = path.lstat()
            info = os.fstat(stream.fileno())
            if identity(info) != identity(current) or not stat.S_ISREG(current.st_mode):
                raise GrowthError("file_replaced_or_path_changed")
            if info.st_size < previous_size:
                raise GrowthError("file_shrank")
            scanned = scan_headers(stream, info.st_size, budget)
            if stop.is_set() or clock() >= deadline:
                break  # Late work is not an observation within the requested window.
            if scanned["headers"][:len(old_headers)] != old_headers:
                raise GrowthError("previous_bounded_header_changed")
            after = os.fstat(stream.fileno())
            if after.st_size < info.st_size:
                raise GrowthError("file_shrank_during_observation")
            pairs = set(scanned["pairs"])
            if initial_pairs is None:
                initial_pairs = len(pairs)
                report["initial_bytes"] = info.st_size
            elif info.st_size > previous_size or pairs != old_pairs:
                report["growth_events"].append({"observed_after_seconds": round(clock() - started, 3),
                    "file_bytes": info.st_size, "bounded_fragment_pairs": len(pairs),
                    "new_fragment_pairs": len(pairs - old_pairs)})
            report.update(observations=report["observations"] + 1, final_bytes=info.st_size,
                movie_header_present=scanned["movie_header_present"], pending=scanned["pending"],
                initial_fragment_pairs=initial_pairs, final_fragment_pairs=len(pairs),
                new_fragment_pairs=len(pairs) - initial_pairs)
            old_headers, old_pairs, previous_size = scanned["headers"], pairs, info.st_size
            pause(min(0.2, max(0, deadline - clock())))
        growth_samples = sum(item["new_fragment_pairs"] > 0 for item in report["growth_events"])
        report["fragment_growth_observations"] = growth_samples
        report.update(status="cancelled" if stop.is_set() else
            ("fragment_growth_observed" if growth_samples >= 2 else "insufficient_fragment_growth"),
            exit_code=130 if stop.is_set() else (0 if growth_samples >= 2 else 2))
    except (OSError, ValueError) as error:
        report.update(status="error", exit_code=1, reason=str(error)[:1024])
    finally:
        try:
            if stream is not None:
                stream.close()
            elif fd is not None:
                os.close(fd)
        except OSError as error:
            report.update(status="cleanup_failed", exit_code=1, cleanup_ok=False, cleanup_error=str(error)[:1024])
        report["header_bytes_read"] = budget.read_bytes
    return report


def validate_worker_report(report, returncode):
    if (not isinstance(report, dict) or report.get("report_kind") != "recording_growth_headers" or
            type(report.get("exit_code")) is not int or report["exit_code"] not in (0, 1, 2, 130) or
            report["exit_code"] != returncode or type(report.get("cleanup_ok")) is not bool or
            any(report.get(key) is not False for key in ("media_payload_read", "image_access_enabled",
                "live_source_verified", "payload_publication_verified", "feedback_exclusion_verified")) or
            type(report.get("images_decoded")) is not int or report["images_decoded"] != 0 or
            (report["exit_code"] == 0 and (report.get("status") != "fragment_growth_observed" or
                                         not report["cleanup_ok"]))):
        raise GrowthError("invalid_growth_worker_report")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--observe", type=Path, metavar="MP4", help="Explicitly observe one operator-selected growing file")
    mode.add_argument("--watch-directory", type=Path, metavar="DIR", help="Wait for one new *_stereo.mp4; do not read existing clips")
    parser.add_argument("--wait-for-new", type=int, choices=range(1, MAX_WAIT_SECONDS + 1), default=15)
    parser.add_argument("--seconds", type=int, choices=range(1, 31), default=15)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    report = new_report()
    if args.observe or args.watch_directory:
        if args.worker:
            stop = stop_on_signals()
            report = (watch_new(args.watch_directory, args.wait_for_new, args.seconds, stop, announce=True)
                      if args.watch_directory else observe(args.observe, args.seconds, stop))
        else:
            selection = (["--watch-directory", str(args.watch_directory), "--wait-for-new", str(args.wait_for_new)]
                         if args.watch_directory else ["--observe", str(args.observe)])
            report = supervise([sys.executable, str(Path(__file__).resolve()), *selection,
                                "--seconds", str(args.seconds), "--worker"], new_report(),
                               args.seconds + (args.wait_for_new if args.watch_directory else 0) + 5,
                               validate_worker_report)
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
