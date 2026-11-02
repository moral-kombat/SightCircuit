"""Write or check tools/hud_contract.hpp, the native copy of the HUD contract."""
import argparse
from pathlib import Path
import sys

if not __package__:  # Run as a file: import this checkout's packages without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from framejacker.hud import native  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--write", action="store_true", help="regenerate the header from the Python contract")
    modes.add_argument("--check", action="store_true", help="exit 1 if the committed header is stale")
    parser.add_argument("--header", type=Path, default=native.HEADER, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.check:
            if native.check(args.header):
                print(f"Current: {args.header}")
                return 0
            print(f"Stale generated header: {args.header}; run python3 scripts/hud_contract_header.py --write "
                  "and rebuild the bridge", file=sys.stderr)
            return 1
        print(f"{'Wrote' if native.write(args.header) else 'Unchanged'}: {args.header}")
    except (OSError, ValueError) as error:
        print(f"HUD contract header not {'checked' if args.check else 'written'}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
