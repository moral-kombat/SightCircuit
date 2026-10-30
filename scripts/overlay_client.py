"""Bounded anonymous-pipe transport to our native public OpenVR overlay bridge."""
from dataclasses import dataclass, replace
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BRIDGE = ROOT / "artifacts/overlay-bridge/framejacker-overlay-bridge"
MAX_SIDE = 512


def packet(kind, payload=b""):
    return struct.pack("<4sII", b"FJO1", kind, len(payload)) + payload


@dataclass(frozen=True)
class PanelSettings:
    width: float = 0.8
    distance: float = 1.5
    x: float = 0.0
    y: float = -0.2
    opacity: float = 0.95
    visible: bool = True

    def encode(self):
        for value, low, high in ((self.width, .25, 1.5), (self.distance, .5, 3),
                                 (self.x, -1, 1), (self.y, -1, 1), (self.opacity, .2, 1)):
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError("Panel setting out of range")
        if not isinstance(self.visible, bool):
            raise ValueError("Panel visibility must be boolean")
        return packet(2, struct.pack("<5fI", self.width, self.distance, self.x,
                                     self.y, self.opacity, int(self.visible)))


def rgba_from_bgr(image):
    import cv2
    import numpy as np

    if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3 or not image.size:
        raise ValueError("Overlay needs a nonempty uint8 BGR image")
    height, width = image.shape[:2]
    ratio = min(1, MAX_SIDE / max(width, height))
    if ratio < 1:
        width, height = max(1, round(width * ratio)), max(1, round(height * ratio))
        image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
    rgba = cv2.cvtColor(image, cv2.COLOR_BGR2RGBA)
    return width, height, rgba.tobytes()


class OverlayClient:
    def __init__(self, binary=DEFAULT_BRIDGE, *, validation_only=False, env=None):
        self.binary, self.validation_only, self.env = Path(binary), validation_only, env
        self.condition = threading.Condition()
        self.settings = PanelSettings()
        self._fixed_settings = None
        self.pending_frame = None
        self.ready = threading.Event()
        self.done = threading.Event()
        self.stopping = threading.Event()
        self.acknowledged = False
        self.error = None
        self.final_report = None
        self.frames_sent = 0
        self.frame_expiry_supported = False
        self.frame_expired = threading.Event()
        self.last_expiry = None
        self.forced_termination = False
        self.process = self.reader = self.writer = None

    def _fail(self, error):
        with self.condition:
            if not self.error:
                self.error = str(error)

    def start(self):
        if not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            raise RuntimeError("Overlay bridge missing; run bash scripts/build_overlay_bridge.sh")
        mode = "--validate-stdin" if self.validation_only else "--stdin"
        self.process = subprocess.Popen([str(self.binary), mode], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, env=self.env, bufsize=0)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        if not self.ready.wait(8) or not self.acknowledged or self.error:
            error = self.error or "Overlay bridge did not initialize within eight seconds"
            self.close()
            raise RuntimeError(error)
        self.writer = threading.Thread(target=self._write, daemon=True)
        self.writer.start()
        return self

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(8192)
                if not line:
                    break
                if not line.endswith(b"\n"):
                    raise RuntimeError("Oversized overlay bridge diagnostic")
                message = json.loads(line)
                event = message.get("event")
                if event == "ready":
                    if (message.get("validation_only") != self.validation_only or
                            message.get("runtime_initialized") != (not self.validation_only)):
                        raise RuntimeError("Overlay bridge initialization mode mismatch")
                    self.acknowledged = True
                    self.frame_expiry_supported = message.get("frame_expiry_supported") is True
                    self.ready.set()
                if event == "frame_expired":
                    self.last_expiry = message
                    self.frame_expired.set()
                if event == "error":
                    self._fail(message.get("error", "Overlay bridge failed"))
                if "exit_code" in message:
                    self.final_report = message
                    if message["exit_code"] or not message.get("cleanup_ok", False):
                        self._fail(message.get("error", "Overlay cleanup failed"))
        except Exception as error:
            self._fail(error)
        finally:
            if not self.final_report:
                self._fail("Overlay bridge disconnected without a final cleanup report")
            self.done.set()
            self.ready.set()

    def _send(self, data):
        view = memoryview(data)
        while view:
            count = self.process.stdin.write(view)
            if not count:
                raise RuntimeError("Overlay bridge pipe closed")
            view = view[count:]

    def _write(self):
        try:
            previous_settings = None
            last_heartbeat = 0
            while True:
                with self.condition:
                    if (self.pending_frame is None and self.settings == previous_settings
                            and not self.stopping.is_set()):
                        self.condition.wait(timeout=1)
                    frame, self.pending_frame = self.pending_frame, None
                    settings = self.settings
                if self.stopping.is_set():
                    self._send(packet(3))
                    break
                if settings != previous_settings or time.monotonic() - last_heartbeat >= 1:
                    self._send(settings.encode())
                    previous_settings, last_heartbeat = settings, time.monotonic()
                if frame:
                    self._send(frame)
                    self.frames_sent += 1
        except (OSError, RuntimeError, ValueError) as error:
            if not self.stopping.is_set():
                self._fail(error)
        finally:
            self.process.stdin.close()

    def set_settings(self, settings):
        settings.encode()
        with self.condition:
            if self._fixed_settings is not None and replace(settings, visible=False) != self._fixed_settings:
                raise ValueError("overlay_settings_frozen")
            self.settings = settings
            self.condition.notify()

    def freeze_settings(self, settings):
        """Pin calibrated candidate geometry; only visibility may change afterward."""
        if not self.validation_only:
            raise ValueError("fixed_live_hud_not_approved")
        settings.encode()
        fixed = replace(settings, visible=False)
        with self.condition:
            if self._fixed_settings is not None and self._fixed_settings != fixed:
                raise ValueError("overlay_settings_frozen")
            self._fixed_settings = fixed
            self.settings = settings
            self.condition.notify()

    def submit_frame(self, width, height, rgba, *, expires_ns=None):
        if not 0 < width <= MAX_SIDE or not 0 < height <= MAX_SIDE or len(rgba) != width * height * 4:
            raise ValueError("Invalid RGBA overlay frame")
        if expires_ns is not None:
            if not self.validation_only or not self.frame_expiry_supported:
                raise ValueError("expiring_live_output_not_approved_or_supported")
            if type(expires_ns) is not int or not 0 < expires_ns <= time.monotonic_ns() + 1000000000:
                raise ValueError("invalid_frame_expiry")
        data = (packet(1, struct.pack("<II", width, height) + rgba) if expires_ns is None else
                packet(4, struct.pack("<QII", expires_ns, width, height) + rgba))
        with self.condition:
            self.pending_frame = data
            self.condition.notify()

    def submit_bgr(self, image, *, expires_ns=None):
        self.submit_frame(*rgba_from_bgr(image), expires_ns=expires_ns)

    def close(self):
        self.stopping.set()
        with self.condition:
            self.condition.notify_all()
        if self.process:
            if self.writer:
                self.writer.join(timeout=2)
            if not self.writer or self.writer.is_alive():
                if self.process.poll() is None:
                    self.forced_termination = True
                    self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.forced_termination = True
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.forced_termination = True
                    self.process.kill()
                    self.process.wait(timeout=2)
            if self.writer:
                self.writer.join(timeout=1)
            if self.reader:
                self.reader.join(timeout=1)
            if (self.writer and self.writer.is_alive()) or (self.reader and self.reader.is_alive()):
                self._fail("Overlay transport thread did not stop")
            if not self.writer or not self.writer.is_alive():
                self.process.stdin.close()
            if not self.reader or not self.reader.is_alive():
                self.process.stdout.close()
            if self.forced_termination:
                self._fail("Overlay bridge required forced termination; cleanup is unconfirmed")
            if self.process.returncode != 0:
                self._fail("Overlay bridge exit disagrees with successful cleanup")
        return {"requested": True, "validation_only": self.validation_only,
                "frames_sent": self.frames_sent, "error": self.error,
                "native_report": self.final_report, "forced_termination": self.forced_termination,
                "worker_reaped": self.process is None or self.process.poll() is not None,
                "worker_exit_code": self.process.returncode if self.process else None,
                "transport_threads_stopped": not any(t and t.is_alive() for t in (self.writer, self.reader))}
