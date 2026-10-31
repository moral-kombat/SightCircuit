"""Run the session helper with generated commands, never a device or runtime."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("bash") and shutil.which("timeout"), "requires bash and timeout")
class SessionFailureTests(unittest.TestCase):
    def run_session(self, stages, fail_match="", fail_code=1):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "scripts"
            scripts.mkdir()
            shutil.copy(ROOT / "scripts/frame_session.sh", scripts / "frame_session.sh")
            python = root / ".venv-replay/bin/python"
            python.parent.mkdir(parents=True)
            python.write_text(
                "#!/usr/bin/env python3\n"
                "import json,os,sys,time\n"
                "from pathlib import Path\n"
                "with Path(os.environ['SESSION_CALLS']).open('a') as f:\n"
                " f.write(json.dumps(sys.argv[1:])+'\\n')\n"
                "match=os.environ.get('SESSION_FAIL_MATCH','')\n"
                "if match and match in ' '.join(sys.argv[1:]):\n"
                " if os.environ['SESSION_FAIL_CODE']=='hang': time.sleep(30)\n"
                " else: sys.exit(int(os.environ['SESSION_FAIL_CODE']))\n"
            )
            python.chmod(0o700)
            calls = root / "calls.jsonl"
            binary_dir = root / "test-bin"
            binary_dir.mkdir()
            sleep = binary_dir / "sleep"
            sleep.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(.2)\n")
            sleep.chmod(0o700)
            env = dict(os.environ, YES="1", SKIP_UPDATE="1", SESSION_CALLS=str(calls), STEP_TIMEOUT="1",
                       PATH=str(binary_dir)+os.pathsep+os.environ["PATH"],
                       SESSION_FAIL_MATCH=fail_match, SESSION_FAIL_CODE=str(fail_code))
            env.pop("COUNTDOWN", None)
            result = subprocess.run(["bash", str(scripts / "frame_session.sh"), *stages],
                                    env=env, capture_output=True, text=True, timeout=10)
            return result, calls.read_text() if calls.exists() else ""

    def test_pacing_failure_prevents_next_command_and_mirror_stage(self):
        result, calls = self.run_session(["pacing", "mirror"], "replay-realtime.json", 7)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("replay-realtime.json", calls)
        self.assertNotIn("4threads", calls)
        self.assertNotIn("mirror_recognize.py", calls)
        self.assertIn("exit 7", result.stdout)
        self.assertIn("Physical headset recovery is unconfirmed", result.stdout)

    def test_overlay_failure_preserves_wearer_question_and_stops_mirror(self):
        result, calls = self.run_session(["overlay", "mirror"], "replay-overlay.json", 124)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("mirror_recognize.py", calls)
        self.assertIn("timed out", result.stdout)
        self.assertIn("passthrough/tracking stay normal", result.stdout)
        self.assertIn("unanswered", result.stdout)

    def test_formatter_failure_is_not_masked_by_successful_replays(self):
        result, calls = self.run_session(["pacing", "mirror"], "- /", 9)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("replay-unpaced.json", calls)
        self.assertNotIn("mirror_recognize.py", calls)
        self.assertIn("some commands failed", result.stdout)

    def test_real_timeout_of_generated_command_stops_next_stage(self):
        result, calls = self.run_session(["overlay", "mirror"], "replay-overlay.json", "hang")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("mirror_recognize.py", calls)
        self.assertIn("exit 124 (timed out)", result.stdout)

    def test_finished_replay_prevents_window_mirror_capture(self):
        result, calls = self.run_session(["window-mirror", "mirror"], "window-replay.json", 6)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("exited before mirror capture (exit 6)", result.stdout)
        self.assertIn("Capture was not started", result.stdout)
        self.assertNotIn("mirror_recognize.py", calls)

    def test_successful_pacing_advances_to_requested_mirror(self):
        result, calls = self.run_session(["pacing", "mirror"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("replay-realtime-4threads.json", calls)
        self.assertIn("replay-unpaced.json", calls)
        self.assertIn("mirror_recognize.py", calls)
        self.assertNotIn("Physical headset recovery is unconfirmed", result.stdout)


if __name__ == "__main__":
    unittest.main()
