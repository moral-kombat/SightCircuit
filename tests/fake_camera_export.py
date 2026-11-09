"""The stand-in for ``sightcircuit-camera-helper --export-frames``: a producer of valid and invalid export streams.

Run as a program with the helper's export arguments (``--export-frames --fps F
--max-seconds S --max-frames M --cameras 0,1``) it writes a camera export v1
stream on stdout and JSON diagnostic lines on stderr, paced at ``fps`` with
real host clocks, ending on the frame or time bound (or on SIGTERM/SIGINT
with reason ``stop_signal`` and exit 130, as the helper does). ``--fake-scenario``
selects a misbehaviour: ``invalid:<name>`` writes one stream of
``sightcircuit.camera.fake_export.INVALID`` (every framing violation, every
field violation, out-of-order kinds, oversize headers and payloads, a
truncated stream, a stream with no end), ``no_end`` exits after the frames
without an end packet, ``hang`` keeps the stream open until a stop signal,
``hang_deaf`` ignores stop signals, ``refuse`` exits 5 with a diagnostic line
and no packet, ``silent_exit`` exits 2 with nothing, ``wrong_limits`` echoes
other limits in ``ready``, ``stderr_noise`` adds non-JSON and many JSON lines
on stderr, ``exit_failure`` ends normally but exits 1, ``headset_removed``
ends early with that reason, ``stale_clock`` stamps every frame five
seconds in the past. The tests install it as an executable file
outside the checkout and point ``camera_helper.run_export`` at it. Never a
camera, never the SDK.
"""
import argparse
import json
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from sightcircuit.camera import fake_export  # noqa: E402

EXIT_CANCELLED, EXIT_REFUSED, EXIT_INVALID_ARGUMENTS = 130, 5, 64
SHEBANG_INSTALL = "#!{python}\nimport sys\nsys.path.insert(0, {root!r})\nfrom tests.fake_camera_export import main\nsys.exit(main())\n"


def install(directory, python=sys.executable):
    """Write the executable stand-in into ``directory`` (outside the checkout); return its path."""
    path = Path(directory) / "sightcircuit-camera-helper-fake"
    path.write_text(SHEBANG_INSTALL.format(python=python, root=str(ROOT)))
    path.chmod(0o755)
    return path


def note(event, **fields):
    sys.stderr.write(json.dumps({"report_kind": "sightcircuit_camera_helper", "event": event, **fields}) + "\n")
    sys.stderr.flush()


def emit(data):
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


class Stopper:
    """Stop signals blocked and collected between writes, as the helper collects them between SDK calls.

    Blocking (not handling) them means no window in which a SIGTERM reaches
    the default disposition, interpreter shutdown included. A ``deaf``
    stopper blocks them and never looks, so only SIGKILL ends it.
    """
    STOPS = {signal.SIGTERM, signal.SIGINT}

    def __init__(self, deaf=False):
        self.signal = None
        self.deaf = deaf
        signal.pthread_sigmask(signal.SIG_BLOCK, self.STOPS)

    def poll(self):
        if not self.deaf and self.signal is None:
            info = signal.sigtimedwait(self.STOPS, 0)
            if info is not None:
                self.signal = info.si_signo
        return self.signal is not None

    def wait(self, seconds):
        """Wait up to ``seconds``; True when a stop signal arrived (and was consumed)."""
        if self.deaf:
            time.sleep(seconds)
            return False
        if self.poll():
            return True
        info = signal.sigtimedwait(self.STOPS, seconds)
        if info is not None:
            self.signal = info.si_signo
        return self.signal is not None


def parse(argv):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--export-frames", action="store_true")
    parser.add_argument("--fps", type=int)
    parser.add_argument("--max-seconds", type=int)
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--cameras", default="0,1")
    parser.add_argument("--fake-scenario", default="valid")
    parser.add_argument("--width", type=int, default=fake_export.WIDTH)
    parser.add_argument("--height", type=int, default=fake_export.HEIGHT)
    parser.add_argument("--stride", type=int, default=None)
    try:
        return parser.parse_args(argv)
    except SystemExit:
        return None


def main(argv=None):
    args = parse(sys.argv[1:] if argv is None else argv)
    if args is None or not args.export_frames or None in (args.fps, args.max_seconds, args.max_frames):
        note("final", reason="invalid_arguments", exit_code=EXIT_INVALID_ARGUMENTS)
        return EXIT_INVALID_ARGUMENTS
    scenario = args.fake_scenario
    # As the helper, stop signals are collected, never delivered to the default disposition; the deaf scenario ignores
    # them. The mask changes this process: run as a program only (the tests exec the installed file).
    stopper = Stopper(deaf=scenario == "hang_deaf")
    limits = {"fps": args.fps, "max_seconds": args.max_seconds, "max_frames": args.max_frames}
    cameras = tuple(int(item) for item in args.cameras.split(","))
    stride = args.stride or args.width * 3 + 8
    if scenario.startswith("invalid:"):
        emit(fake_export.INVALID[scenario.split(":", 1)[1]][0]())
        note("final", reason="fake_invalid_stream", scenario=scenario)
        return 0
    if scenario == "refuse":
        note("final", reason="confirmation_required", exit_code=EXIT_REFUSED)
        return EXIT_REFUSED
    if scenario == "silent_exit":
        return 2
    note("ready", backend="fake", limits=limits)
    echoed = dict(limits, max_frames=limits["max_frames"] + 1) if scenario == "wrong_limits" else limits
    emit(fake_export.encode(fake_export.ready_header(limits=echoed, cameras=cameras)))
    if scenario == "stderr_noise":
        sys.stderr.write("plain text the helper should never write\n")
        sys.stderr.write("x" * 9000 + "\n")
        for index in range(300):
            note("outcome", call="acquire", outcome="ok", pass_number=index)
    started = time.monotonic()
    deadline = started + limits["max_seconds"]
    exported, reason = 0, "max_frames"
    while exported < limits["max_frames"]:
        if time.monotonic() >= deadline:
            reason = "max_seconds"
            break
        if stopper.poll():
            reason = "stop_signal"
            break
        exported += 1
        stale = 5_000_000_000 if scenario == "stale_clock" else 0  # Acquired five seconds ago: older than the input limit.
        header = fake_export.frame_header(exported, width=args.width, height=args.height, stride=stride,
                                          camera_index=cameras[(exported - 1) % len(cameras)],
                                          monotonic_ns=time.monotonic_ns() - stale)
        emit(fake_export.encode(header, fake_export.pixels(exported, args.width, args.height, stride)))
        if scenario == "headset_removed" and exported == 2:
            reason = "headset_removed"
            break
        if stopper.wait(1 / limits["fps"]):
            reason = "stop_signal"
            break
    if scenario == "no_end":
        note("final", reason="fake_no_end")
        return 0
    if scenario in ("hang", "hang_deaf"):
        note("hanging")
        while not stopper.wait(3600):
            pass
        reason = "stop_signal"
    if reason != "stop_signal" and stopper.signal is not None:
        reason = "stop_signal"
    emit(fake_export.encode(fake_export.end_header(exported, reason=reason)))
    note("final", reason=reason, exported=exported, exit_code=0)
    if reason == "stop_signal":
        return EXIT_CANCELLED
    return 1 if scenario == "exit_failure" else 0


if __name__ == "__main__":
    sys.exit(main())
