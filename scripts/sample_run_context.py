"""Sample headset load, heat and power as CSV beside a latency run (read-only sidecar).

Reads only /proc and /sys: no OpenVR, camera, SteamVR process or service is
touched. One row per interval: UTC with offset and monotonic nanoseconds from
one clock reading, CPU load, thermal zones, throttle state, clock caps, GPU
frequency, power rails, battery and available memory. A missing reading is a
blank cell. The Frame paths are unconfirmed on SteamOS 0.5.6 (upstream tested
0.4.5). Rows are run context, not latency or device evidence. SIGINT or
SIGTERM ends the run after the current row. This is not a Tier A runner task.
"""
import argparse
import json
import os
from pathlib import Path
import sys

if not __package__:  # Run as a file: import this checkout's packages without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sightcircuit.runtime import sensors  # noqa: E402
from sightcircuit.runtime.guard import stop_on_signals  # noqa: E402


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    low, high = sensors.INTERVAL_LIMITS
    parser.add_argument("--interval", type=float, default=5.0,
                        help=f"seconds between rows, {low:g} to {high:g} (default 5)")
    parser.add_argument("--samples", type=int, default=720,
                        help=f"rows to write, 1 to {sensors.MAX_SAMPLES} (default 720: one hour at 5 s)")
    parser.add_argument("--output", type=Path, metavar="NEW_CSV",
                        help="write to this new file (never overwritten) instead of stdout")
    parser.add_argument("--root", type=Path, default=Path("/"),
                        help="read /proc and /sys under this directory (tests use a fake tree; default /)")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    low, high = sensors.INTERVAL_LIMITS
    if not low <= args.interval <= high or not 1 <= args.samples <= sensors.MAX_SAMPLES:
        print(json.dumps({"status": "invalid_arguments", "exit_code": 64}), file=sys.stderr)
        return 64
    stop = stop_on_signals()
    sampler = sensors.SensorSampler(args.root)
    if args.output is None:
        written = sensors.run(sampler, sys.stdout, interval=args.interval, samples=args.samples, stop=stop)
    else:
        try:
            descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        except OSError as error:
            print(json.dumps({"status": "output_refused", "error": error.strerror, "exit_code": 1}), file=sys.stderr)
            return 1
        with os.fdopen(descriptor, "w", newline="") as output:
            written = sensors.run(sampler, output, interval=args.interval, samples=args.samples, stop=stop)
    # The summary goes to stderr so stdout stays pure CSV.
    print(json.dumps({"report_kind": "run_context_sensor_sampler", "status": "complete" if written == args.samples
                      else "stopped", "rows": written, "columns": len(sampler.columns),
                      "scope": sensors.UNCONFIRMED_SCOPE, "exit_code": 0}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
