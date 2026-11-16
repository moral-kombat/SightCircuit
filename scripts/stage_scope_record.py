"""Write one one-run scope record for an approved camera HUD device step (work-order item L10).

For the Frame manager, when staging a step the owner approved in the Frame
manager's session. It is deliberately not an operator command: it has no body
in ``sightcircuit/commands``, no console entry point, and no command imports
it or calls ``scope.write_record``. Writing a record is not an approval and
opens no gate. It is a procedural control against mistakes, not a
cryptographic one: anyone who can run this on the Frame can write a record,
so the owner's approval stays the authority and ``--approval`` only names it.

The record is bound to this checkout's commit and to the checkpoint manifest,
which must carry the camera HUD's group and verify now (run
``scripts/checkpoint_manifest.py --write PATH --camera-hud`` after the builds
first). One record serves one run of one step; a re-run is a new
approval and a new record.
"""
import argparse
import json
from pathlib import Path
import sys

if not __package__:  # Run as a file: import this checkout's packages without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sightcircuit.runtime import scope  # noqa: E402


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--step", required=True, choices=sorted(scope.STEPS), help="the approved device step")
    parser.add_argument("--approval", required=True, metavar="IDENTIFIER",
                        help="reference to the owner's approval: an identifier, never free text")
    parser.add_argument("--checkpoint-manifest", required=True, type=Path, metavar="PATH",
                        help="the manifest written on this machine after the builds; it must verify now")
    parser.add_argument("--valid-minutes", required=True, type=int, metavar=f"1..{scope.MAX_VALID_MINUTES}",
                        help="how long from now the record's absolute UTC deadline lies")
    parser.add_argument("--max-seconds", type=int, metavar="SECONDS",
                        help="the run's duration bound; default and maximum are the step's own")
    parser.add_argument("--out", required=True, type=Path, metavar="PATH",
                        help="new .json file for the record, outside the checkout or in an ignored directory")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        record = scope.write_record(args.out, step=args.step, approval=args.approval,
                                    checkpoint_manifest=args.checkpoint_manifest, valid_minutes=args.valid_minutes,
                                    max_seconds=args.max_seconds)
    except (OSError, scope.ScopeError) as error:
        print(f"Scope record not written: {getattr(error, 'reason', 'os_error')}: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "written", "record": str(args.out), **record}, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
