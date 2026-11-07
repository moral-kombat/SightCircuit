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
from sightcircuit.runtime import guard as bounded_worker
from scripts import recording_growth_check as growth


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
        if mode in ("slow_arm", "never_arms"):
            # Small budgets: the arm wait and the escalation are what these modes measure.
            grace = bounded_worker.Grace(arm=0.8, cooperative=1.0, terminate=0.3, kill=0.3, report=0.3)
            report = bounded_worker.supervise([sys.executable, __file__, "worker", mode, directory],
                                              growth.new_report(), 10, growth.validate_worker_report, grace=grace)
            print(json.dumps(report), flush=True)
            return report["exit_code"]
        if mode == "progress":
            snapshots = []
            report = bounded_worker.supervise([sys.executable, __file__, "worker", mode, directory],
                                              growth.new_report(), 10, growth.validate_worker_report,
                                              progress=snapshots.append)
            report["progress_snapshots"] = snapshots
            print(json.dumps(report), flush=True)
            return report["exit_code"]
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
                growth.validate_worker_report, descendants=mode in ("orphan_helper", "parent_death", "guardian_death"))
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

    if mode == "slow_arm":
        # The window the cancellation test's probe can hit: forked, exec'd, but no handler yet.
        (root / "started").write_text(str(os.getpid()))
        time.sleep(0.4)
        stop = bounded_worker.stop_on_signals()
        (root / "ready").write_text(str(os.getpid()))
        stop.wait(10)
        print(json.dumps(dict(growth.new_report(), status="cancelled", exit_code=130, cleanup_ok=True)), flush=True)
        return 130
    if mode == "progress":
        stop = bounded_worker.stop_on_signals()
        for count in range(3):
            print(json.dumps({"event": "progress", "report": {"progress_count": count + 1}}), flush=True)
        report = dict(growth.new_report(), status="insufficient_fragment_growth", exit_code=2, progress_count=3)
        print(json.dumps({"event": "result", "report": report}), flush=True)
        return 2
    stop = threading.Event()
    if mode == "cooperative":
        stop = bounded_worker.stop_on_signals(stop)  # Arms the supervisor's handshake as every guarded worker does.
    else:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    (root / "ready").write_text(str(os.getpid()))
    if mode == "never_arms":
        time.sleep(60)
    if mode == "partial":
        sys.stdout.write('{"report_kind":')
        sys.stdout.flush()
        time.sleep(60)
    if mode == "oversized":
        sys.stdout.buffer.write(b"x" * (bounded_worker.MAX_REPORT_BYTES + 4096))
        sys.stdout.flush()
        time.sleep(60)
    if mode in ("orphan_helper", "parent_death", "guardian_death"):
        subprocess.Popen([sys.executable, __file__, "helper", mode, directory])
        wait_for(root / "helper")
    if mode in ("parent_death", "guardian_death"):
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
