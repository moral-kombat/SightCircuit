"""The synthetic stand-in recording that realtime replay pacing is measured on.

E62 measured ``replay_window.py --realtime`` on a synthetic 2464 x 2464 HEVC
clip at 60 fps, 10 s long, with a one-second GOP and about 320 Mbit/s, which
decodes on cloud CPUs about as slowly as Frame's recordings do on Frame. Its
generator was not committed. ``write_standin`` encodes a clip to those
documented parameters (``STANDIN``); the content is new: seeded drifting noise
with grey chroma, so its exact bitrate and decode cost differ from E62's clip.
Runner-CPU input only: it describes no Frame recording, core or decoder.
"""
from pathlib import Path
import time

STANDIN = {"side": 2464, "fps": 60, "seconds": 10, "gop_frames": 60, "bitrate_kbps": 320_000}
SCOPE = ("Synthetic stand-in to E62's documented clip parameters for runner-CPU pacing checks; "
         "not a Frame recording")


def _valid(side, fps, seconds, gop_frames, bitrate_kbps):
    values = (side, fps, seconds, gop_frames, bitrate_kbps)
    return (all(type(value) is int for value in values) and 64 <= side <= 4096 and side % 2 == 0 and
            1 <= fps <= 120 and 1 <= seconds <= 60 and 1 <= gop_frames <= fps * seconds and
            100 <= bitrate_kbps <= 1_000_000)


def write_standin(path, *, side=STANDIN["side"], fps=STANDIN["fps"], seconds=STANDIN["seconds"],
                  gop_frames=STANDIN["gop_frames"], bitrate_kbps=STANDIN["bitrate_kbps"], seed=0):
    """Encode the stand-in to the new file ``path`` with libx265 and return its description.

    The file is created exclusively (an existing path raises ``FileExistsError``
    and is left alone) and removed again if encoding fails. Keyframes fall
    exactly every ``gop_frames`` frames; the bitrate is x265's average target.
    """
    if not _valid(side, fps, seconds, gop_frames, bitrate_kbps):
        raise ValueError("standin_parameters_invalid")
    path = Path(path)
    import av
    import numpy as np

    started = time.monotonic()
    frames = fps * seconds
    rng = np.random.default_rng(seed)
    texture = rng.integers(0, 128, (side, side + 3 * frames), dtype=np.uint8)
    chroma = np.full((side // 2, side), 128, dtype=np.uint8)  # U then V planes, as yuv420p rows.
    params = (f"log-level=error:keyint={gop_frames}:min-keyint={gop_frames}:scenecut=0:"
              f"bitrate={bitrate_kbps}:vbv-maxrate={bitrate_kbps}:vbv-bufsize={bitrate_kbps}")
    with open(path, "xb") as handle:
        try:
            with av.open(handle, mode="w", format="mp4") as output:
                stream = output.add_stream("libx265", rate=fps)
                stream.width = stream.height = side
                stream.pix_fmt = "yuv420p"
                stream.options = {"preset": "ultrafast", "x265-params": params}
                for index in range(frames):
                    # Texture drifting 3 px per frame plus fresh noise: little for motion search to predict.
                    luma = texture[:, 3 * index:3 * index + side] + rng.integers(0, 128, (side, side), dtype=np.uint8)
                    frame = av.VideoFrame.from_ndarray(np.concatenate((luma, chroma)), format="yuv420p")
                    frame.pts = index
                    for packet in stream.encode(frame):
                        output.mux(packet)
                for packet in stream.encode():
                    output.mux(packet)
        except BaseException:
            handle.close()
            path.unlink()
            raise
    size = path.stat().st_size
    return {"report_kind": "replay_standin", "path": str(path), "codec": "hevc", "width": side, "height": side,
            "fps": fps, "frames": frames, "gop_frames": gop_frames, "target_bitrate_kbps": bitrate_kbps,
            "bytes": size, "measured_bitrate_kbps": round(size * 8 / seconds / 1000),
            "encode_seconds": round(time.monotonic() - started, 3), "pyav_version": av.__version__, "scope": SCOPE}
