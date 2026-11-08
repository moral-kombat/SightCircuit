"""Drive the Frame agent guard over the Phase 2 enforcement matrix offline; print a report of counts.

Thin wrapper: the command body is ``sightcircuit.commands.agent_enforcement_check``
(also the ``sightcircuit-agent-enforcement-check`` console entry point of an
installed package). Imported as ``scripts.agent_enforcement_check``, this module
is that body, so ``main(argv)`` and its module attributes are the body's own.
"""
from pathlib import Path
import sys

if not __package__:  # Run as a file: import this checkout's package without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sightcircuit.commands import agent_enforcement_check as _command  # noqa: E402

main = _command.main

if __name__ == "__main__":
    sys.exit(main())
else:
    sys.modules[__name__] = _command
