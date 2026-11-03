"""Compare fills for the native HUD mask with the real detectors; write a JSON report.

The library is ``framejacker/mask_fill_evaluation.py``. Inputs are the
synthetic HUD source's uniform gray frames with the fixture calibration and the
seed stills with a pasted mask. Results are runner-CPU measurements, never
Frame or headset results. With ``--expect-clean-gray-sweep`` the run fails
(exit 1) when the runtime fill lets any detector box the mask on the sweep.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys

if not __package__:  # Run as a file: import this checkout's packages without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from framejacker import mask_fill_evaluation as fills
from framejacker.detection import DETECTORS, create_detector, describe
from framejacker.errors import FramejackerError

ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = ROOT / ".cache/framejacker/evaluation"
DEFAULT_MANIFEST = ROOT / "evaluation/seed.json"
SCOPE = ("Runner CPU on generated 96x64 gray frames (synthetic fixture calibration) and public seed stills with a "
         "pasted mask; not Frame, headset or composited-mirror evidence")


def source_revision():
    try:
        revision = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=ROOT, check=True,
                                  capture_output=True, text=True, timeout=5).stdout.strip()
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, check=True,
                                capture_output=True, text=True, timeout=5).stdout
        diff = subprocess.run(["git", "diff", "--binary", "HEAD"], cwd=ROOT, check=True, capture_output=True,
                              timeout=10).stdout
    except (OSError, subprocess.SubprocessError) as error:
        return {"revision": None, "error": str(error)}
    return {"revision": revision, "working_tree_dirty": bool(status),
            "tracked_diff_sha256": hashlib.sha256(diff).hexdigest()}


def environment():
    import cv2
    import numpy as np

    versions = {"python": platform.python_version(), "platform": platform.platform(), "machine": platform.machine(),
                "opencv": cv2.__version__, "numpy": np.__version__}
    try:
        import onnxruntime
        versions["onnxruntime"] = onnxruntime.__version__
    except ImportError:
        versions["onnxruntime"] = None
    return versions


def print_summary(rows):
    columns = ("detector", "fill", "gray_levels_with_mask_overlap", "cases_with_mask_overlap",
               "cases_with_new_mask_overlap", "cases_with_artifact_like_overlap", "lost", "added", "label_changed",
               "score_changed")
    table = [columns] + [tuple(str(row.get(column, "-")) for column in columns) for row in rows]
    widths = [max(len(line[index]) for line in table) for index in range(len(columns))]
    for line in table:
        print("  ".join(value.ljust(width) for value, width in zip(line, widths)).rstrip(), file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detector", choices=DETECTORS, action="append", help="Repeatable; default: all")
    parser.add_argument("--fill", choices=fills.FILLS, action="append", help="Repeatable; default: all")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="Seed manifest (evaluation/seed.json)")
    parser.add_argument("--fetch", action="store_true", help="Download missing checksum-pinned seed images")
    parser.add_argument("--no-seeds", action="store_true", help="Gray sweep, timing and leak checks only")
    parser.add_argument("--gray-levels", choices=("odd", "all"), default="odd",
                        help="odd: E76's 127 odd levels (default); all: every level 0-254 the source emits")
    parser.add_argument("--timing-runs", type=int, default=30, choices=range(1, 1001), metavar="N")
    parser.add_argument("--report", type=Path, help="Also save the JSON report to this new file")
    parser.add_argument("--expect-clean-gray-sweep", action="store_true",
                        help="Exit 1 if any detector boxes the mask on the sweep with the runtime fill")
    args = parser.parse_args(argv)
    if args.report and args.report.exists():
        parser.error("--report must name a new file")
    selected = tuple(dict.fromkeys(args.fill or fills.FILLS))
    if args.expect_clean_gray_sweep and fills.RUNTIME_FILL not in selected:
        parser.error(f"--expect-clean-gray-sweep needs the runtime fill ({fills.RUNTIME_FILL}) among --fill")
    report = {"report_kind": "hud_mask_fill_evaluation", "schema_version": 1, "status": "error", "scope": SCOPE,
              "code": source_revision(), "manifest": None if args.no_seeds else str(args.manifest),
              "gray_levels": args.gray_levels}
    try:
        report["environment"] = environment()
        images = [] if args.no_seeds else fills.load_seed_images(
            args.manifest, DOWNLOADS, args.fetch, log=lambda message: print(message, file=sys.stderr))
        detectors = {name: create_detector(name) for name in dict.fromkeys(args.detector or DETECTORS)}
        report["detectors"] = {name: describe(detector) for name, detector in detectors.items()}
        levels = fills.ODD_GRAY_LEVELS if args.gray_levels == "odd" else fills.ALL_GRAY_LEVELS
        report.update(fills.evaluate(detectors, images, selected, levels, timing_runs=args.timing_runs))
        report["summary"] = fills.summary(report)
        print_summary(report["summary"])
        report["status"] = "evaluated"
    except (ImportError, OSError, ValueError, RuntimeError, FramejackerError) as error:
        report["error"] = str(error)
    text = json.dumps(report, indent=2)
    print(text)
    if args.report:
        try:
            with args.report.open("x") as output:
                output.write(text + "\n")
        except OSError as error:
            print(f"Could not save report: {error}", file=sys.stderr)
            return 1
    if report["status"] != "evaluated":
        return 1
    if args.expect_clean_gray_sweep:
        dirty = [name for name, sweep in report["gray_sweep"].items()
                 if sweep["fills"][fills.RUNTIME_FILL]["mask_overlap_level_count"]]
        if dirty:
            print(f"runtime fill {fills.RUNTIME_FILL}: mask boxed on the gray sweep by {', '.join(dirty)}",
                  file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
