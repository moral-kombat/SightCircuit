"""Inspect MP4 recording headers without decoding or reading media/pose payloads."""
import argparse
import json
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from framejacker.sources.recording import RecordingError, inspect_recording, latest_recording  # noqa: E402

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", help="MP4 path; defaults to newest Arcturus stereo recording")
    args = parser.parse_args(argv)
    path = None
    try:
        path = Path(args.path).expanduser() if args.path else latest_recording(
            Path.home() / "Videos" / "ArcturusVisionCamera")
        report = inspect_recording(path)
    except (OSError, RecordingError) as error:
        report = {"report_kind": "arcturus_recording_metadata", "schema_version": 1,
                  "scope": "MP4 headers only; no video decoding performed", "status": "error",
                  "path": str(path) if path is not None else None, "error": str(error)}
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
