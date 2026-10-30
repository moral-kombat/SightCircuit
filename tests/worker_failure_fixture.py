"""Local subprocesses for deterministic lifecycle failures; never device input."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import bounded_worker, recording_growth_check as growth


def wait_for(path):
    deadline = time.monotonic() + 5
    while not path.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("fixture_handshake_timeout")
        time.sleep(0.005)


def main():
    role, mode, directory = sys.argv[1:]
    root = Path(directory)
    if role == "parent":
        if mode.startswith("pipeline_hung_"):
            from scripts.live_pipeline import supervise_synthetic
            report = supervise_synthetic({"seconds": 30, "detector": "fixture",
                "test_fault": mode.removeprefix("pipeline_"), "test_ready_file": str(root / "ready")})
            print(json.dumps(report), flush=True)
            return report["exit_code"]
        unrelated = None
        if mode == "orphan_helper":
            unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            report = bounded_worker.supervise([sys.executable, __file__, "worker", mode, directory],
                growth.new_report(), 2 if mode == "partial" else 10,
                growth.validate_worker_report, descendants=mode in ("orphan_helper", "parent_death"))
            if unrelated:
                report["unrelated_child_untouched"] = unrelated.poll() is None
        finally:
            if unrelated:
                unrelated.kill()
                unrelated.wait(timeout=2)
        print(json.dumps(report), flush=True)
        return report["exit_code"]
    if role == "helper":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        (root / "helper").write_text(str(os.getpid()))
        time.sleep(60)
        return 0

    stop = threading.Event()
    signal.signal(signal.SIGTERM, (lambda *_: stop.set()) if mode == "cooperative" else signal.SIG_IGN)
    (root / "ready").write_text(str(os.getpid()))
    if mode == "partial":
        sys.stdout.write('{"report_kind":')
        sys.stdout.flush()
        time.sleep(60)
    if mode == "oversized":
        sys.stdout.buffer.write(b"x" * (bounded_worker.MAX_REPORT_BYTES + 4096))
        sys.stdout.flush()
        time.sleep(60)
    if mode in ("orphan_helper", "parent_death"):
        subprocess.Popen([sys.executable, __file__, "helper", mode, directory])
        wait_for(root / "helper")
    if mode == "parent_death":
        time.sleep(60)
    report = dict(growth.new_report(), status="insufficient_fragment_growth", exit_code=2)
    if mode == "cooperative":
        stop.wait(10)
        (root / "stopping").write_text("ready")
        wait_for(root / "finish")
        report.update(status="cancelled", exit_code=130)
    if mode == "unsafe_claim":
        report["image_access_enabled"] = True
    print(json.dumps(report), flush=True)
    return 7 if mode == "nonzero_after_report" else report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
