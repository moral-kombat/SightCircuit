"""Discover a SteamVR virtual mirror; opt in to bounded, read-only acquisition."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from framejacker.runtime import process  # noqa: E402
from framejacker.runtime.process import StopRequested, stop_on_signal  # noqa: E402,F401 - StopRequested: shared export

from framejacker.sources.mirror import capture, new_capture_report as new_report  # noqa: E402
from framejacker.sources.mirror_device import MirrorError, discover, grayscale_png, select_candidate  # noqa: E402


def _worker(candidate, seconds, preview_temp, sender, stop, expected_parent):
    def send(report, event="progress"):
        process.send_report(sender, report, event)

    report = new_report()
    report["candidate"] = candidate
    try:
        # Prevent a stranded worker if its launcher is killed during a driver call.
        if not process.enter_worker(expected_parent):
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
    """Run ``worker`` under the shared bounded-process supervisor; the report names its candidate."""
    return process.run_bounded(candidate, seconds, preview_temp, worker=worker, timeout=timeout,
                               report_factory=lambda: dict(report_factory(), candidate=candidate),
                               success_status=success_status)


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
    old_term = signal.signal(signal.SIGTERM, stop_on_signal)
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
