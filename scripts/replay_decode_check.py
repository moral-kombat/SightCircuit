"""Decode a few frames from one recorded video track, without camera/runtime access.

The decoder runs in a child process under ``sightcircuit.runtime.guard.supervise``
(an owned process group, a 45-second deadline, reaping and, on Linux, the
parent-loss guardian), which accepts its JSON report only through
``validate_worker_report``.
"""
import argparse
import json
from pathlib import Path
import sys

if not __package__:  # Run as a file: import this checkout's packages without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sightcircuit.runtime.guard import supervise  # noqa: E402
from sightcircuit.sources.recording import RecordingError, latest_recording  # noqa: E402

DECODE_SECONDS = 45


def new_report(path, video_stream, frame_limit):
    return {
        "report_kind": "recorded_video_decode",
        "schema_version": 1,
        "path": str(path),
        "status": "error",
        "video_stream_ordinal": video_stream,
        "frames_requested": frame_limit,
        "frames_decoded": 0,
        "frames": [],
        "scope": "Selected recorded track only; camera identity, HDR appearance and stereo alignment unvalidated",
        "images_saved": False,
    }


def decode(path, video_stream, frame_limit):
    report = new_report(path, video_stream, frame_limit)
    try:
        if not path.is_file():
            raise RecordingError("Recording path must be a regular file")
        try:
            import av
        except ImportError:
            report.update(status="dependency_missing", error="PyAV is unavailable in this interpreter",
                          next_step="Run bash scripts/setup_replay.sh, then use .venv-replay/bin/python")
            return report
        report["pyav_version"] = av.__version__
        with av.open(str(path), mode="r") as container:
            videos = list(container.streams.video)
            report["video_track_count"] = len(videos)
            if video_stream >= len(videos):
                raise RecordingError("Selected video stream is absent")
            stream = videos[video_stream]
            stream.codec_context.thread_count = 2
            report.update(container_stream_index=stream.index, track_id=stream.id,
                          codec=stream.codec_context.name)
            for frame in container.decode(stream):
                if frame.width <= 0 or frame.height <= 0 or frame.width * frame.height > 64 * 1024 * 1024 // 3:
                    raise RecordingError("Decoded frame dimensions exceed the RGB buffer limit")
                if frame.is_corrupt:
                    raise RecordingError("Decoder marked a frame as corrupt")
                rgb = frame.reformat(format="rgb24")
                if not rgb.planes or rgb.planes[0].buffer_size <= 0:
                    raise RecordingError("RGB conversion returned no image buffer")
                report["frames"].append({
                    "width": frame.width, "height": frame.height,
                    "source_pixel_format": frame.format.name,
                    "converted_pixel_format": rgb.format.name,
                    "rgb_stride_bytes": rgb.planes[0].line_size,
                    "media_time_seconds": float(frame.pts * frame.time_base)
                    if frame.pts is not None and frame.time_base is not None else None,
                })
                report["frames_decoded"] = len(report["frames"])
                if len(report["frames"]) >= frame_limit:
                    break
        if not report["frames"]:
            raise RecordingError("No video frames decoded")
        report["status"] = "decoded"
    except Exception as error:
        report["status"] = "error"
        report["error"] = str(error)
    return report


def worker_report(report):
    """The decoder child's report: its exit code and cleanup (the container closed when ``decode`` returned)."""
    return dict(report, exit_code=0 if report["status"] == "decoded" else 1, cleanup_ok=True)


def validate_worker_report(report, returncode):
    """Accept only a decode report whose exit code, status and claims agree with the child's exit status."""
    if (not isinstance(report, dict) or report.get("report_kind") != "recorded_video_decode" or
            type(report.get("exit_code")) is not int or report["exit_code"] not in (0, 1) or
            report["exit_code"] != returncode or type(report.get("cleanup_ok")) is not bool or
            report.get("images_saved") is not False or type(report.get("frames_decoded")) is not int or
            (report["exit_code"] == 0) != (report.get("status") == "decoded" and report["frames_decoded"] > 0)):
        raise RecordingError("invalid_decode_worker_report")


def supervision_error(report):
    """A failed supervision's ``error``: a timeout, a decoder exit status other than 0 or 1, else the reason."""
    code = report.get("worker_exit_code")
    if report["status"] == "timed_out":
        return f"File decode exceeded the {DECODE_SECONDS}-second limit"
    if report["status"] != "cancelled" and type(code) is int and code not in (0, 1):
        return f"Decoder process exited with code {code}"
    return str(report.get("reason") or f"Decoder supervision ended {report['status']}")


def run_worker(path, video_stream, frame_limit):
    """Decode in a supervised child; a failed supervision keeps the report's ``error`` field."""
    report = supervise([sys.executable, str(Path(__file__).resolve()), str(path), "--video-stream", str(video_stream),
                        "--frames", str(frame_limit), "--worker"],
                       new_report(path, video_stream, frame_limit), DECODE_SECONDS, validate_worker_report)
    if report["exit_code"] and "error" not in report:
        report["error"] = supervision_error(report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", help="File path; defaults to latest Arcturus stereo recording")
    parser.add_argument("--video-stream", type=int, default=0, help="Zero-based video-track ordinal; no left/right assumption")
    parser.add_argument("--frames", type=int, default=3, choices=range(1, 11), metavar="1..10")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.video_stream < 0:
        parser.error("--video-stream must be nonnegative")
    try:
        path = (Path(args.path).expanduser() if args.path else latest_recording(
            Path.home() / "Videos" / "ArcturusVisionCamera")).resolve()
        if args.worker:
            report = worker_report(decode(path, args.video_stream, args.frames))
        else:
            report = run_worker(path, args.video_stream, args.frames)
    except (OSError, ValueError) as error:
        report = {"report_kind": "recorded_video_decode", "schema_version": 1,
                  "status": "error", "error": str(error), "exit_code": 1}
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
