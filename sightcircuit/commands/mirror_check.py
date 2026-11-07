"""Discover a SteamVR virtual mirror; opt in to bounded, read-only acquisition."""
import argparse
import json
from pathlib import Path
import sys

if not __package__:  # Run as a file (self-spawned worker): import the package beside this one.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sightcircuit.runtime import output, process  # noqa: E402
from sightcircuit.sources.mirror import capture, launch, new_capture_report as new_report  # noqa: E402
from sightcircuit.sources.mirror_device import discover, grayscale_png  # noqa: E402


def _worker(candidate, seconds, outputs, sender, stop, expected_parent):
    """``outputs`` maps "preview" to its reservation; the PNG is written after the source closes."""
    def send(report, event="progress"):
        process.send_report(sender, report, event)

    outputs = outputs or {}
    report = new_report()
    report["candidate"] = candidate
    try:
        # Prevent a stranded worker if its launcher is killed during a driver call.
        if not process.enter_worker(expected_parent):
            return
        report, preview = capture(candidate, seconds, stop, send, "preview" in outputs)
        if preview is not None and "preview" in outputs:
            # Capture has closed the video FD before any image encoding/writing.
            output.write(outputs["preview"], grayscale_png(*preview))
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
    report = dict(new_report(), status="discovery_only", discovery=discovery, exit_code=0)
    if args.capture:
        report = launch(report, discovery, args.device, args.seconds, worker=_worker, report_factory=new_report,
                        success_status="captured",
                        outputs={"preview": (args.preview, ".sightcircuit-mirror-")} if args.preview else None)
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
