"""Claude Code PreToolUse hook for an agent on Frame: allow only the fixed task runner.

Configured by sightcircuit/agent/frame-settings.json; reads the tool call as JSON
on stdin, exits 2 with a reason to block it. See docs/agent-on-frame.md.
"""
from pathlib import Path
import sys

if not __package__:  # Run as a file: import this checkout's packages without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sightcircuit.agent.guard import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
