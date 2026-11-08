"""Bounded empirical marker-copy observation, never a production calibration.

Thin wrapper: the command body is ``sightcircuit.commands.hud_marker_observer`` (also the
``sightcircuit-hud-marker-observer`` console entry point of an installed package).
Imported as ``scripts.hud_marker_observer``, this module is that body, so ``main(argv)`` and
its module attributes are the body's own.
"""
from pathlib import Path
import sys

if not __package__:  # Run as a file: import this checkout's package without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sightcircuit.commands import hud_marker_observer as _command  # noqa: E402

main = _command.main

if __name__ == "__main__":
    sys.exit(main())
else:
    sys.modules[__name__] = _command
