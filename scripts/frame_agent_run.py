"""Run fixed, bounded on-device test tasks by ID; the only command a Frame agent may run.

Usage: python3 scripts/frame_agent_run.py list | run TASK... | run-tier tier-a |
       update REVISION | report latest
See docs/agent-on-frame.md and docs/on-device.md#run-automated-checks-with-an-on-device-agent.
"""
from pathlib import Path
import sys

if not __package__:  # Run as a file: import this checkout's packages without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from framejacker.agent.runner import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
