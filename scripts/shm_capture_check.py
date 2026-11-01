"""Inspect an existing MRHAND luma file; explicitly opt in to bounded reading."""
import argparse
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time

if not __package__:  # Run as a file: import this checkout's packages without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from framejacker.sources.mirror_device import grayscale_png  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
READER = ROOT / "artifacts/shm-reader/framejacker-shm-reader"
DEFAULT_SOURCE = Path("/dev/shm/mrhand-pt")


class StopRequested(BaseException):
    def __init__(self, number):
        self.number = number


def new_report():
    return {"report_kind": "luma_shared_memory_check", "schema_version": 1,
            "status": "not_started", "exit_code": 1, "images_saved": False,
            "read_attempted": False, "cleanup_ok": True,
            "writer_ordering_verified": False, "camera_frame_integrity_verified": False,
            "source_identity": "unverified", "producer_started": False,
            "scope": "Existing luma publications only; timestamps describe observation, not camera exposure"}


def inspect_source(source):
    """Metadata only: never open a file, device, producer or runtime."""
    try:
        info = source.lstat()
        kind = ("regular_file" if stat.S_ISREG(info.st_mode) else
                "symlink" if stat.S_ISLNK(info.st_mode) else "nonregular")
        return {"path": str(source), "present": True, "kind": kind,
                "size_bytes": info.st_size, "header_read": False}
    except FileNotFoundError:
        return {"path": str(source), "present": False, "header_read": False}


def run_reader(source, seconds, camera=0, frames=0, max_age_ms=1000,
               preview_pgm=None, *, binary=READER, timeout=None):
    """A native crash or a blocked mapping cannot take down the supervisor."""
    report = new_report()
    process = None
    started = time.monotonic()
    forced = False
    interrupted = None
    output = b""
    try:
        if not binary.is_file():
            report.update(status="unavailable", reason="reader_not_built",
                          next_step="bash scripts/build_shm_reader.sh")
            return report
        command = [str(binary), "--read", str(source), "--seconds", str(seconds),
                   "--camera", str(camera), "--frames", str(frames),
                   "--max-age-ms", str(max_age_ms)]
        if preview_pgm is not None:
            command += ["--preview-pgm", str(preview_pgm)]
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        report.update(read_attempted=True, reader_pid=process.pid)
        try:
            output, _ = process.communicate(timeout=seconds + 3 if timeout is None else timeout)
        except subprocess.TimeoutExpired:
            report.update(status="timed_out", reason="reader_deadline")
        else:
            if process.returncode < 0:
                report.update(status="error", reason="reader_terminated_by_signal",
                              native_signal=-process.returncode, cleanup_ok=False)
            else:
                try:
                    if len(output) > 16384:
                        raise ValueError("oversized native report")
                    native = json.loads(output)
                    if (native["report_kind"] != "framejacker_shm_reader" or
                            native["exit_code"] != process.returncode):
                        raise ValueError("inconsistent native report")
                    report["native"] = native
                    report.update(status=native["status"], reason=native["reason"],
                                  cleanup_ok=native["cleanup_ok"], exit_code=process.returncode)
                except (KeyError, TypeError, ValueError):
                    report.update(status="error", reason="invalid_reader_report", cleanup_ok=False)
    except (KeyboardInterrupt, StopRequested) as error:
        interrupted = error.number if isinstance(error, StopRequested) else signal.SIGINT
        report.update(status="cancelled", reason="signal", signal=interrupted)
    except OSError as error:
        report.update(status="error", reason="reader_launch_failed", error=str(error))
    finally:
        if process is not None:
            if process.poll() is None:
                forced = True
                process.terminate()
                try:
                    process.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.communicate(timeout=1)
                    except subprocess.TimeoutExpired:
                        report.update(status="error", reason="reader_did_not_exit")
            report["reader_exit_code"] = process.poll()
        if forced:
            report["cleanup_ok"] = False
        report.update(reader_forced_termination=forced,
                      elapsed_ms=round((time.monotonic() - started) * 1000, 3))
        report["exit_code"] = (128 + interrupted if interrupted else
                               0 if report["status"] == "captured" and report["cleanup_ok"] else 1)
    return report


def encode_preview(pgm):
    """Accept only the bounded PGM emitted by our own native reader."""
    with pgm.open("rb") as source:
        if source.readline(8) != b"P5\n":
            raise ValueError("invalid preview header")
        size = source.readline(32).split()
        if len(size) != 2:
            raise ValueError("invalid preview dimensions")
        width, height = map(int, size)
        if not (1 <= width <= 640 and 1 <= height <= 640) or source.readline(8) != b"255\n":
            raise ValueError("invalid preview bounds")
        pixels = source.read(width * height + 1)
        if len(pixels) != width * height:
            raise ValueError("invalid preview payload")
    return grayscale_png(width, height, pixels), width, height


def _stop_signal(number, _frame):
    raise StopRequested(number)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read", action="store_true", help="Read an existing producer file; default only inspects metadata")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--camera", type=int, choices=(0, 1), default=0)
    parser.add_argument("--seconds", type=int, choices=range(1, 11), default=10, metavar="1..10")
    parser.add_argument("--frames", type=int, choices=range(0, 1001), default=0, metavar="0..1000")
    parser.add_argument("--max-age-ms", type=int, choices=range(1, 10001), default=1000, metavar="1..10000")
    parser.add_argument("--preview", type=Path, help="New grayscale PNG, written only after the reader has closed its source")
    args = parser.parse_args(argv)
    if args.preview and not args.read:
        parser.error("--preview requires --read")
    source = args.source.expanduser().absolute()
    report = new_report()
    old_term = signal.signal(signal.SIGTERM, _stop_signal)
    try:
        discovery = inspect_source(source)
        report.update(status="discovery_only", source=discovery, exit_code=0)
        if args.read:
            with tempfile.TemporaryDirectory(prefix="framejacker-shm-") as temporary:
                pgm = Path(temporary) / "preview.pgm" if args.preview else None
                if args.preview:
                    target = args.preview.expanduser().absolute()
                    if (target.suffix.lower() != ".png" or not target.parent.is_dir() or
                            target.exists() or target.is_symlink()):
                        raise ValueError("Preview needs a new .png path in an existing directory")
                report = run_reader(source, args.seconds, args.camera, args.frames, args.max_age_ms, pgm)
                report["source"] = discovery
                if args.preview and report["exit_code"] == 0:
                    png, width, height = encode_preview(pgm)
                    # Publish atomically without replacing any existing target, even in a race.
                    fd, name = tempfile.mkstemp(prefix=".framejacker-shm-", suffix=".png.part", dir=target.parent)
                    try:
                        with os.fdopen(fd, "wb") as output:
                            output.write(png)
                        os.link(name, target, follow_symlinks=False)
                    finally:
                        Path(name).unlink()
                    report.update(images_saved=True, preview_path=str(target),
                                  preview_width=width, preview_height=height)
    except (OSError, ValueError) as error:
        report.update(status="error", reason="source_or_output_failed", error=str(error), exit_code=1)
    except (KeyboardInterrupt, StopRequested) as error:
        number = error.number if isinstance(error, StopRequested) else signal.SIGINT
        report.update(status="cancelled", reason="signal", signal=number, exit_code=128 + number)
    finally:
        signal.signal(signal.SIGTERM, old_term)
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
