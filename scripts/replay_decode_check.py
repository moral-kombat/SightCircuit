"""Decode a few frames from one recorded video track, without camera/runtime access."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

if not __package__:  # Run as a file: import this checkout's packages without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from framejacker.sources.recording import RecordingError, latest_recording  # noqa: E402


def decode(path, video_stream, frame_limit):
    report = {
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
            report = decode(path, args.video_stream, args.frames)
        else:
            try:
                result = subprocess.run([
                    sys.executable, str(Path(__file__).resolve()), str(path),
                    "--video-stream", str(args.video_stream), "--frames", str(args.frames), "--worker",
                ], stdout=subprocess.PIPE, text=True, timeout=45)
            except subprocess.TimeoutExpired:
                raise RecordingError("File decode exceeded the 45-second limit") from None
            if result.returncode not in (0, 1):
                raise RecordingError(f"Decoder process exited with code {result.returncode}")
            report = json.loads(result.stdout)
    except (OSError, ValueError) as error:
        report = {"report_kind": "recorded_video_decode", "schema_version": 1,
                  "status": "error", "error": str(error)}
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "decoded" else 1


if __name__ == "__main__":
    sys.exit(main())
