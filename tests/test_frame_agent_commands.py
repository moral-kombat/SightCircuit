"""Every Tier A command in ``framejacker/agent/tasks.py`` parses with its script's own parser.

``test_frame_agent.py`` checks that each script exists and carries no opt-in
flag. Here each argument vector, with its placeholders filled, goes to the
script's real ``main``; parsing is intercepted right after the parser runs, so
no discovery, device, model or capture code starts. A renamed or removed flag
then fails in CI instead of on Frame.
"""
import argparse
from contextlib import redirect_stderr
import importlib
import io
from pathlib import Path
import unittest
from unittest.mock import patch

from framejacker.agent.tasks import TIER_A

ROOT = Path(__file__).resolve().parents[1]


class Parsed(Exception):
    """Raised by the intercepted parser once a command line has parsed."""


def parse_error(script, argv):
    """None when ``scripts/<script>.py`` accepts ``argv`` with exact option names, else why not."""
    module = importlib.import_module(f"scripts.{script}")
    real = argparse.ArgumentParser.parse_known_args

    def intercept(parser, args=None, namespace=None):
        raise Parsed(parser, *real(parser, args, namespace))

    stderr = io.StringIO()
    with patch.object(argparse.ArgumentParser, "parse_known_args", intercept), redirect_stderr(stderr):
        try:
            module.main(list(argv))
        except Parsed as parsed:
            parser, _, extra = parsed.args
        except SystemExit as error:
            return f"exit {error.code}: {stderr.getvalue().strip()}"
        else:
            return "main returned without parsing its arguments"
    if extra:
        return f"unrecognized arguments: {extra}"
    # argparse accepts unambiguous prefixes; a renamed option must not pass as one.
    inexact = [word.split("=", 1)[0] for word in argv
               if word.startswith("--") and word.split("=", 1)[0] not in parser._option_string_actions]
    return f"options are not exact names: {inexact}" if inexact else None


def filled(task):
    return [part.format(venv_python=".venv-replay/bin/python", run_dir="/tmp/agent-run/task") for part in task.argv]


class AgentTaskCommandTests(unittest.TestCase):
    def test_every_tier_a_command_exists_and_parses(self):
        for task in TIER_A:
            argv = filled(task)
            with self.subTest(task=task.id, argv=argv):
                if argv[0] == "bash":
                    self.assertEqual(len(argv), 2)
                    self.assertTrue((ROOT / argv[1]).is_file(), argv[1])
                elif argv[1:3] == ["-m", "unittest"]:
                    self.assertEqual(argv[3:], ["discover", "-s", "tests"])
                else:
                    script = Path(argv[1])
                    self.assertEqual(script.parent, Path("scripts"))
                    self.assertTrue((ROOT / script).is_file(), script)
                    self.assertIsNone(parse_error(script.stem, argv[2:]))

    def test_flag_drift_is_reported(self):
        for script, argv in (("replay_window", ["--max-frame", "30"]),
                             ("replay_window", ["--realtime", "--decoder-threads", "4"]),
                             ("service_rehearsal", ["--pre"]),
                             ("evaluate_detectors", ["--manifests", "evaluation/local/frame.json"]),
                             ("hud_marker_check", ["--validate", "--secs", "3"])):
            with self.subTest(script=script, argv=argv):
                self.assertIsNotNone(parse_error(script, argv))


if __name__ == "__main__":
    unittest.main()
