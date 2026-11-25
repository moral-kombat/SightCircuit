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

It also names exactly what the run may show and through which view (work-order
item B5), and a run that differs in any of them is refused with the record
unspent: ``--layout``, one of the bridge's fixed layouts, with no default;
for a camera step ``--region`` and ``--rotation`` (the whole frame, unturned,
when not given), ``--presence-watch required`` or ``none``, with no default,
and on a panel layout ``--alignment``, its five numbers, with no default
either (the neutral alignment is ``1,1,0,0,0``). The alignment is the
project's own numbers, set by the wearer; it is never the SDK's calibration.
The synthetic step opens no camera and takes none of the four.
"""
import argparse
import json
from pathlib import Path
import sys

if not __package__:  # Run as a file: import this checkout's packages without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sightcircuit.hud.contract import FIXED_LAYOUTS, fixed_layout  # noqa: E402
from sightcircuit.runtime import scope  # noqa: E402

PRESENCE_WATCH = {"required": True, "none": False}


def numbers(text, count):
    """``count`` comma-separated numbers as floats; ``argparse`` reports anything else."""
    parts = text.split(",")
    if len(parts) != count:
        raise argparse.ArgumentTypeError(f"needs {count} comma-separated numbers")
    try:
        return [float(part) for part in parts]
    except ValueError:
        raise argparse.ArgumentTypeError(f"needs {count} comma-separated numbers") from None


def region(text):
    return numbers(text, 4)


def alignment(text):
    """``SCALE_X,SCALE_Y,OFFSET_X,OFFSET_Y,TURN`` as the record's alignment; the turn is a whole number."""
    *values, turn = numbers(text, len(scope.ALIGNMENT_FIELDS))
    if turn != int(turn):
        raise argparse.ArgumentTypeError("the turn is 0, 90, 180 or 270")
    return dict(zip(scope.ALIGNMENT_FIELDS, (*values, int(turn))))


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
    parser.add_argument("--layout", required=True, choices=[layout.layout_id for layout in FIXED_LAYOUTS],
                        help="the fixed layout the run may show; a run with any other is refused")
    parser.add_argument("--region", type=region, metavar="X0,Y0,X1,Y1",
                        help="camera steps: the region of the frame the detector is given, as fractions "
                             "(default: the whole frame)")
    parser.add_argument("--rotation", type=int, choices=scope.ROTATIONS,
                        help="camera steps: the clockwise quarter turn applied to the region (default 0)")
    parser.add_argument("--alignment", type=alignment, metavar="SX,SY,OX,OY,TURN",
                        help="camera steps on a panel layout, required there: the wearer's alignment, the project's "
                             "own five numbers (neutral is 1,1,0,0,0)")
    parser.add_argument("--presence-watch", choices=sorted(PRESENCE_WATCH),
                        help="camera steps, required there: whether the run must hold the presence watch")
    parser.add_argument("--out", required=True, type=Path, metavar="PATH",
                        help="new .json file for the record, outside the checkout or in an ignored directory")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        record = scope.write_record(args.out, step=args.step, approval=args.approval,
                                    checkpoint_manifest=args.checkpoint_manifest, valid_minutes=args.valid_minutes,
                                    max_seconds=args.max_seconds, hud_config=fixed_layout(args.layout),
                                    region=args.region, rotation=args.rotation, alignment=args.alignment,
                                    presence_watch=PRESENCE_WATCH.get(args.presence_watch))
    except (OSError, scope.ScopeError) as error:
        print(f"Scope record not written: {getattr(error, 'reason', 'os_error')}: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "written", "record": str(args.out), **record}, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
