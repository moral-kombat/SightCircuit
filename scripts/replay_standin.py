"""Write the synthetic realtime-replay stand-in (E62's clip parameters) to a new MP4 and print its report.

The ARM64 pacing job replays this clip on the runner's CPU; it is never a Frame
recording. Defaults are 2464 x 2464 HEVC, 60 fps, 10 s, a one-second GOP and a
320 Mbit/s target; ``--side`` and ``--seconds`` make a smaller clip for trials.
"""
import argparse
import json
from pathlib import Path
import sys

if not __package__:  # Run as a file: import this checkout's packages without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sightcircuit.sources.standin import STANDIN, write_standin  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New .mp4 path; an existing file is refused")
    parser.add_argument("--side", type=int, default=STANDIN["side"], help="Square frame side; default 2464")
    parser.add_argument("--seconds", type=int, default=STANDIN["seconds"], help="Clip length; default 10")
    args = parser.parse_args(argv)
    report = {"report_kind": "replay_standin", "status": "error", "path": str(args.output)}
    try:
        if args.output.suffix.lower() != ".mp4":
            raise ValueError("Stand-in output must use .mp4")
        report.update(write_standin(args.output, side=args.side, seconds=args.seconds), status="written")
    except FileExistsError:
        report["error"] = f"Output already exists: {args.output}"
    except (ImportError, OSError, ValueError) as error:
        report["error"] = str(error)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "written" else 1


if __name__ == "__main__":
    sys.exit(main())
