"""Bounded CPU recognition from frames the Arcturus camera helper exports, without display.

Thin wrapper: the command body is ``sightcircuit.commands.camera_recognize`` (also the
``sightcircuit-camera-recognize`` console entry point of an installed package).
Imported as ``scripts.camera_recognize``, this module is that body, so ``main(argv)`` and
its module attributes are the body's own.
"""
from pathlib import Path
import sys

if not __package__:  # Run as a file: import this checkout's package without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sightcircuit.commands import camera_recognize as _command  # noqa: E402

main = _command.main

if __name__ == "__main__":
    sys.exit(main())
else:
    sys.modules[__name__] = _command
