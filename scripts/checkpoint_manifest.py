"""Write or verify the checkpoint artifact manifest: software identity only.

Run both on Frame. After the native build, --write pins this checkout, the
interpreter, its packages and the bridge built here to a new file. Just before
the run, --verify (read-only) exits 1 on any difference or a dirty tree. Built
binaries are never transferred. A manifest is not device or qualification
evidence.
"""
import argparse
import json
from pathlib import Path
import sys

if __name__ == "__main__":
    sys.dont_write_bytecode = True  # --verify writes nothing, not even bytecode caches.
if not __package__:  # Run as a file: import this checkout's packages without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sightcircuit.runtime import manifest  # noqa: E402


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--write", type=Path, metavar="PATH",
                       help="pin the current state to a new file; never overwrites")
    modes.add_argument("--verify", type=Path, metavar="PATH",
                       help="read-only: print the differences from PATH as JSON; exit 1 on any")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="with --write: pin a dirty tree, recorded as dirty: true (it never verifies)")
    parser.add_argument("--camera-hud", action="store_true",
                        help="with --write: carry the camera HUD's group (the SDK helper and the presence watcher "
                             "built here, their sources and scripts), which a one-run scope record requires; a "
                             "missing helper or watcher is then a problem. Without it the group is carried only "
                             "when the helper exists, with the watcher when that exists too and without it, and "
                             "without a problem, when it does not")
    parser.add_argument("--root", type=Path, default=manifest.ROOT, help=argparse.SUPPRESS)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.allow_dirty and args.write is None:
        parser.error("--allow-dirty applies only to --write")
    if args.camera_hud and args.write is None:
        parser.error("--camera-hud applies only to --write; --verify checks the groups the manifest carries")
    if args.verify is not None:
        report = manifest.verify_manifest(args.verify, args.root)
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
        return report["exit_code"]
    try:
        result = manifest.write_manifest(args.write, args.root, allow_dirty=args.allow_dirty,
                                         camera_hud=True if args.camera_hud else None)
    except (OSError, manifest.ManifestError) as error:
        print(f"Checkpoint manifest not written: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    if result["dirty"] or result["problems"]:
        print("Recorded a dirty tree or missing files/packages; --verify fails until they are fixed.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
