"""Discover a SteamVR virtual mirror; opt in to bounded, read-only acquisition."""
import argparse
import ctypes
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import signal
import sys
import tempfile
import time

if __package__:
    from .mirror_capture import (MirrorError, NativeMirror, discover, grayscale_png,
                                 grayscale_sample, select_candidate)
else:
    from mirror_capture import (MirrorError, NativeMirror, discover, grayscale_png,
                                grayscale_sample, select_candidate)


class StopRequested(BaseException):
    def __init__(self, number):
        self.number = number


def new_report():
    return {"report_kind": "steamvr_mirror_check", "schema_version": 1,
            "status": "not_started", "images_saved": False,
            "device_open_attempted": False, "acquisition_started": False,
            "frames_read": 0, "grayscale_sample_changes": 0,
            "read_attempts": 0, "would_block_reads": 0, "interrupted_reads": 0,
            "reader_method": "nonblocking_read_without_poll_gate",
            "unchanged_sample_reads": 0, "short_reads": 0,
            "maximum_reads_per_second": 10, "cleanup_ok": True,
            "sample_change_proves_live_camera": False,
            "scope": "Composited mirror only; live passthrough, camera identity and image freshness require device observations",
            "physical_camera_identity": "unverified", "camera_timestamps_available": False}


def capture(candidate, seconds, stop, progress=lambda report: None, want_preview=False,
            backend_factory=None, on_frame=None, *, max_fps=10, deadline=None):
    """Acquisition loop; injectable backend permits real lifecycle/failure tests."""
    if not math.isfinite(max_fps) or not 0 < max_fps <= 10 or (deadline is not None and not math.isfinite(deadline)):
        raise ValueError("invalid_capture_pacing_or_deadline")
    report = new_report()
    report.update(status="opening", candidate=candidate, seconds_requested=seconds)
    report["maximum_reads_per_second"] = max_fps
    progress(report.copy())
    backend = None
    last_frame = None
    last_digest = None
    started = last_read = None
    max_gap = 0.0
    try:
        if stop.is_set() or (deadline is not None and time.monotonic() >= deadline):
            report["status"] = "cancelled" if stop.is_set() else "deadline_expired"
            return report, None
        backend = (backend_factory or NativeMirror)(candidate)
        report["device_open_attempted"] = True
        backend.open()
        report.update(acquisition_started=True, status="acquiring",
                      capabilities=backend.capabilities, format=backend.format)
        progress(report.copy())
        started = time.monotonic()
        deadline = min(started + seconds, deadline) if deadline is not None else started + seconds
        next_read, next_progress = started, started + 1
        while not stop.is_set() and time.monotonic() < deadline:
            now = time.monotonic()
            if now < next_read:
                stop.wait(min(next_read - now, deadline - now, 0.05))
                continue
            next_read = now + 1 / max_fps
            report["read_attempts"] += 1
            frame = backend.read_frame(min(0.05, deadline - now))
            report.update(getattr(backend, "read_diagnostics", {}))
            if frame is not None:
                if len(frame) < backend.format["minimum_buffer_bytes"]:
                    report["short_reads"] += 1
                    continue
                _, _, sample = grayscale_sample(frame, backend.format, 32)
                digest = hashlib.blake2b(sample, digest_size=16).digest()
                if last_digest is not None:
                    report["grayscale_sample_changes" if digest != last_digest else "unchanged_sample_reads"] += 1
                last_digest = digest
                report["frames_read"] += 1
                observed = time.monotonic()
                if last_read is None:
                    report["first_frame_after_ms"] = round((observed - started) * 1000, 3)
                else:
                    max_gap = max(max_gap, observed - last_read)
                last_read = observed
                if on_frame is not None:
                    on_frame(frame, backend.format, report["frames_read"], time.monotonic_ns())
                if want_preview:
                    last_frame = frame
            if time.monotonic() >= next_progress:
                progress(report.copy())
                next_progress = time.monotonic() + 1
        report["status"] = "cancelled" if stop.is_set() else ("captured" if report["frames_read"] else "no_frames")
    except Exception as error:
        report.update(status="error", reason=getattr(error, "reason", "acquisition_failed"), error=str(error))
    finally:
        if backend is not None:
            report.update(getattr(backend, "read_diagnostics", {}))
            if backend.capabilities is not None:
                report["capabilities"] = backend.capabilities
            if getattr(backend, "observed_format", None) is not None:
                report["observed_format"] = backend.observed_format
            try:
                backend.close()
            except Exception as error:
                report.update(status="error", cleanup_ok=False, reason="close_failed", error=str(error))
        if started is not None:
            elapsed = time.monotonic() - started
            report["acquisition_elapsed_ms"] = round(elapsed * 1000, 3)
            report["observed_read_rate_hz"] = round(report["frames_read"] / max(elapsed, 0.001), 3)
            report["maximum_read_gap_ms"] = round(max(max_gap, time.monotonic() - last_read if last_read else elapsed) * 1000, 3)
    # This is sampled image change, not a producer sequence or exposure check.
    preview = None
    if report["status"] == "captured" and last_frame is not None:
        preview = grayscale_sample(last_frame, backend.format, 640)
    return report, preview


def _worker(candidate, seconds, preview_temp, sender, stop, expected_parent):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)

    def send(report, event="progress"):
        sender.send_bytes(json.dumps({"event": event, "report": report}).encode())

    report = new_report()
    report["candidate"] = candidate
    try:
        # Prevent a stranded worker if its launcher is killed during a driver call.
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "Could not enable parent-death signaling")
        if os.getppid() != expected_parent:
            return
        report, preview = capture(candidate, seconds, stop, send, bool(preview_temp))
        if preview is not None and preview_temp:
            # Capture has closed the video FD before any image encoding/writing.
            fd = os.open(preview_temp["path"], os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if (info.st_dev, info.st_ino) != tuple(preview_temp["identity"]):
                    raise MirrorError("preview_changed", "Owned preview temporary file was replaced")
                with os.fdopen(fd, "wb") as output:
                    fd = None
                    output.write(grayscale_png(*preview))
            finally:
                if fd is not None:
                    os.close(fd)
            report.update(preview_ready=True, preview_width=preview[0], preview_height=preview[1],
                          preview_pixel_format="gray8")
        send(report, "result")
    except Exception as error:
        report.update(status="error", reason=getattr(error, "reason", "worker_failed"), error=str(error))
        try:
            send(report, "result")
        except (OSError, BrokenPipeError):
            pass
    finally:
        sender.close()


def run_bounded(candidate, seconds, preview_temp=None, *, worker=_worker, timeout=None,
                report_factory=new_report, success_status="captured"):
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    stop = ctx.Event()
    process = ctx.Process(target=worker, args=(candidate, seconds, preview_temp, sender, stop, os.getpid()), daemon=True)
    report = report_factory()
    report.update(candidate=candidate, seconds_requested=seconds)
    result_seen = False
    forced = False
    interrupted = None
    started = time.monotonic()
    deadline = started + (seconds + 3 if timeout is None else timeout)
    try:
        process.start()
        report["worker_pid"] = process.pid
        sender.close()
        while time.monotonic() < deadline:
            if receiver.poll(min(0.05, max(0, deadline - time.monotonic()))):
                try:
                    message = json.loads(receiver.recv_bytes(16384))
                except EOFError:
                    break
                report.update(message["report"])
                if message["event"] == "result":
                    result_seen = True
                    break
            if not process.is_alive():
                if receiver.poll():
                    continue
                break
        if not result_seen:
            expired = time.monotonic() >= deadline
            report.update(status="timed_out" if expired else "error",
                          reason="worker_deadline" if expired else "worker_exited_without_result")
    except (KeyboardInterrupt, StopRequested) as error:
        interrupted = error.number if isinstance(error, StopRequested) else signal.SIGINT
        report.update(status="cancelled", reason="signal", signal=interrupted)
    except Exception as error:
        report.update(status="error", reason="worker_failed", error=str(error))
    finally:
        stop.set()
        if process.pid is not None:
            process.join(timeout=0.5)
            if process.is_alive():
                forced = True
                process.terminate()
                process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
            if process.is_alive():
                report.update(status="error", reason="worker_did_not_exit")
            report["worker_exit_code"] = process.exitcode
            if result_seen and process.exitcode != 0:
                report.update(status="error", reason="worker_exit_failure", worker_exit_code=process.exitcode)
            if not process.is_alive():
                process.close()
        sender.close()
        receiver.close()
    report.update(worker_forced_termination=forced, worker_elapsed_ms=round((time.monotonic() - started) * 1000, 3))
    if forced or not result_seen:
        report["cleanup_ok"] = False  # OS closes FDs, but cooperative close was not confirmed.
    report["exit_code"] = (128 + interrupted if interrupted else
                           0 if report["status"] == success_status and report["cleanup_ok"] else 1)
    return report


def _stop_signal(number, _frame):
    raise StopRequested(number)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", action="store_true", help="Open only a verified virtual mirror; default is sysfs discovery only")
    parser.add_argument("--device", help="Explicit candidate path if several exist; no physical camera fallback")
    parser.add_argument("--seconds", type=int, default=10, choices=range(1, 11), metavar="1..10")
    parser.add_argument("--preview", type=Path, help="Explicit new PNG path; grayscale still is published after acquisition stops")
    args = parser.parse_args(argv)
    if not args.capture and (args.device or args.preview):
        parser.error("--device and --preview require --capture")
    discovery = discover()
    report = new_report()
    report.update(status="discovery_only", discovery=discovery, exit_code=0)
    if not args.capture:
        print(json.dumps(report, indent=2))
        return 0
    temporary = None
    old_term = signal.signal(signal.SIGTERM, _stop_signal)
    try:
        candidate = select_candidate(discovery, args.device)
        if args.preview:
            target = args.preview.expanduser().absolute()
            if target.suffix.lower() != ".png" or not target.parent.is_dir() or target.exists() or target.is_symlink():
                raise MirrorError("invalid_preview_path", "Preview needs a new .png path in an existing directory")
            fd, name = tempfile.mkstemp(prefix=".framejacker-mirror-", suffix=".png.part", dir=target.parent)
            info = os.fstat(fd)
            os.close(fd)
            temporary = {"path": name, "identity": [info.st_dev, info.st_ino]}
        report = run_bounded(candidate, args.seconds, temporary)
        report["discovery"] = discovery
        if temporary and report.get("preview_ready") and report["exit_code"] == 0:
            # Hard-link publication is atomic and refuses to overwrite any target.
            os.link(temporary["path"], target, follow_symlinks=False)
            report.update(images_saved=True, preview_path=str(target))
    except MirrorError as error:
        report.update(status="unavailable" if error.reason == "mirror_unavailable" else "error",
                      reason=error.reason, error=str(error), exit_code=1)
    except (OSError, ValueError) as error:
        report.update(status="error", reason="output_or_launch_failed", error=str(error), exit_code=1)
    except (KeyboardInterrupt, StopRequested) as error:
        number = error.number if isinstance(error, StopRequested) else signal.SIGINT
        report.update(status="cancelled", reason="signal", signal=number, exit_code=128 + number)
    finally:
        if temporary:
            path = Path(temporary["path"])
            try:
                info = path.lstat()
                if (info.st_dev, info.st_ino) == tuple(temporary["identity"]):
                    path.unlink()
            except FileNotFoundError:
                pass
            except OSError as error:
                report.update(status="error", reason="preview_cleanup_failed", error=str(error), exit_code=1)
        signal.signal(signal.SIGTERM, old_term)
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
