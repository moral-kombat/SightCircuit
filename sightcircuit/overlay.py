"""Bounded anonymous-pipe transport to our native public OpenVR overlay bridge."""
from dataclasses import dataclass, replace
import json
import math
import os
from pathlib import Path
import select
import struct
import subprocess
import threading
import time

from .gates import LIVE_HUD_OUTPUT
from .hud.contract import (BRIDGE_READY_WAIT_SECONDS, EXPIRING_FRAME_PREFIX_FORMAT, FRAME_PREFIX_FORMAT, HUD_CONFIG,
                           MAX_DISPLAY_AGE_NS, MAX_SIDE, OPCODE_EXPIRING_FRAME, OPCODE_FRAME, OPCODE_SETTINGS, OPCODE_STOP,
                           PACKET_HEADER_FORMAT, PACKET_MAGIC, RGBA_BYTES, SETTINGS_FORMAT)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BRIDGE = ROOT / "artifacts/overlay-bridge/sightcircuit-overlay-bridge"
# (name, low, high); the native bridge takes these from the generated tools/hud_contract.hpp.
PANEL_LIMITS = (("width", .25, 1.5), ("distance", .5, 3), ("x", -1, 1), ("y", -1, 1), ("opacity", .2, 1))


def packet(kind, payload=b""):
    return struct.pack(PACKET_HEADER_FORMAT, PACKET_MAGIC, kind, len(payload)) + payload


def _zero(value):
    return type(value) is int and value == 0


def bridge_closed_cleanly(report):
    """The one "bridge closed cleanly" check for an ``OverlayClient.close()`` report.

    True only when the helper was reaped with exit 0, both transport threads
    stopped, nothing was forced or failed, and the native final report claims
    exit 0 and cleanup. A missing field is a failure. It is software cleanup,
    never physical display recovery.
    """
    native = report.get("native_report") or {}
    return (report.get("worker_reaped") is True and _zero(report.get("worker_exit_code")) and
            report.get("transport_threads_stopped") is True and report.get("forced_termination") is False and
            report.get("error") is None and native.get("cleanup_ok") is True and _zero(native.get("exit_code")))


@dataclass(frozen=True)
class PanelSettings:
    width: float = 0.8
    distance: float = 1.5
    x: float = 0.0
    y: float = -0.2
    opacity: float = 0.95
    visible: bool = True

    def encode(self):
        for name, low, high in PANEL_LIMITS:
            value = getattr(self, name)
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError("Panel setting out of range")
        if not isinstance(self.visible, bool):
            raise ValueError("Panel visibility must be boolean")
        return packet(OPCODE_SETTINGS, struct.pack(SETTINGS_FORMAT, self.width, self.distance, self.x,
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
        self.stop_requested = False
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
        if os.name == "posix":
            os.set_blocking(self.process.stdin.fileno(), False)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        # The bridge's connect budget plus its margin: a bridge that gives up names the stop first.
        if not self.ready.wait(BRIDGE_READY_WAIT_SECONDS) or not self.acknowledged or self.error:
            error = self.error or f"Overlay bridge did not initialize within {BRIDGE_READY_WAIT_SECONDS} seconds"
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
        """Bound each POSIX pipe write, including a bridge that stops reading."""
        view = memoryview(data)
        deadline = time.monotonic() + 2
        while view:
            if os.name == "posix":
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("overlay_write_timeout")
                if not select.select([], [self.process.stdin], [], min(remaining, .05))[1]:
                    continue
            try:
                count = self.process.stdin.write(view)
            except BlockingIOError:
                continue
            if count is None and os.name == "posix":
                continue
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
                    self._send(packet(OPCODE_STOP))
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
        LIVE_HUD_OUTPUT.require(self.validation_only is True, "fixed_live_hud_not_approved")
        settings.encode()
        fixed = replace(settings, visible=False)
        with self.condition:
            if self._fixed_settings is not None and self._fixed_settings != fixed:
                raise ValueError("overlay_settings_frozen")
            self._fixed_settings = fixed
            self.settings = settings
            self.condition.notify()

    def submit_frame(self, width, height, rgba, *, expires_ns=None):
        if not 0 < width <= MAX_SIDE or not 0 < height <= MAX_SIDE or len(rgba) != width * height * RGBA_BYTES:
            raise ValueError("Invalid RGBA overlay frame")
        if expires_ns is not None:
            # Expiring frames are permitted only on a validation bridge that supports expiry.
            LIVE_HUD_OUTPUT.require(self.validation_only is True and self.frame_expiry_supported is True,
                                    "expiring_live_output_not_approved_or_supported")
            if type(expires_ns) is not int or not 0 < expires_ns <= time.monotonic_ns() + MAX_DISPLAY_AGE_NS:
                raise ValueError("invalid_frame_expiry")
        data = (packet(OPCODE_FRAME, struct.pack(FRAME_PREFIX_FORMAT, width, height) + rgba) if expires_ns is None else
                packet(OPCODE_EXPIRING_FRAME, struct.pack(EXPIRING_FRAME_PREFIX_FORMAT, expires_ns, width, height) + rgba))
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
            running = self.process.poll() is None
            if self.writer:
                self.writer.join(timeout=2)
            cooperative = self._request_stop()
            self.stop_requested = self.stop_requested or (running and cooperative)
            self._end_process(cooperative=cooperative)
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
        report = {"requested": True, "validation_only": self.validation_only,
                  "physical_display_recovery_verified": False,
                  "frames_sent": self.frames_sent, "error": self.error,
                  "native_report": self.final_report, "forced_termination": self.forced_termination,
                  # A cooperative stop request and SIGTERM/SIGKILL escalation, reported apart.
                  "stop_requested": self.stop_requested, "escalated": self.forced_termination,
                  "worker_reaped": self.process is None or self.process.poll() is not None,
                  "worker_exit_code": self.process.returncode if self.process else None,
                  "transport_threads_stopped": not any(t and t.is_alive() for t in (self.writer, self.reader))}
        report["cleanup_confirmed"] = bridge_closed_cleanly(report)
        return report

    def _request_stop(self):
        """True once the writer has sent the stop packet and closed stdin (EOF)."""
        return bool(self.writer) and not self.writer.is_alive()

    def _wait_cooperative(self):
        """Bounded wait for the helper to exit after a stop request."""
        try:
            self.process.wait(timeout=2)
            return True
        except subprocess.TimeoutExpired:
            return False

    def _end_process(self, cooperative):
        if cooperative and self._wait_cooperative():
            return
        if self.process.poll() is not None:
            return
        self.forced_termination = True
        self.process.terminate()
        try:
            self.process.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            pass
        self.process.kill()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._fail("Overlay bridge did not exit after kill; cleanup is unconfirmed")



class OverlaySink:
    """Pipeline sink presenting each result on the VR panel.

    With ``client``, the caller owns that client: its start, placement,
    visibility and close (the replay window's interactive panel); the sink only
    submits images. Without one, the sink owns a validation-only client: it
    starts it on ``open``, shows the panel on ``submit``, hides it on ``clear``
    and returns ``True`` from ``close`` only for a proven clean validation
    shutdown. ``hud`` (the fixed HUD configuration) also freezes the HUD
    placement and accepts only HUD tiles drawn for it; real fixed-HUD output
    stays behind the ``LIVE_HUD_OUTPUT`` gate.
    """

    def __init__(self, client=None, *, hud=None, client_factory=None):
        if hud is not None and client is not None:
            raise ValueError("hud_sink_requires_owned_validation_client")
        self.client, self.hud = client, hud
        self.owns_client = client is None
        self.client_factory = client_factory or OverlayClient
        self.report = None

    def placement(self, visible):
        if self.hud is None:
            return PanelSettings(visible=visible)
        c = HUD_CONFIG  # Even failure cleanup uses the frozen placement.
        return PanelSettings(width=c.panel_width_m, distance=c.distance_m, x=c.x_m, y=c.y_m,
                             opacity=c.opacity, visible=visible)

    def check_contract(self, config):
        """The fixed HUD contract: frozen configuration, validation client and placement."""
        if config != HUD_CONFIG or self.hud != config:
            raise ValueError("hud_sink_config_changed")
        if self.client is not None and (self.client.validation_only is not True or
                                        self.client.settings != self.placement(self.client.settings.visible)):
            raise ValueError("hud_sink_mode_or_placement_changed")

    def open(self):
        if not self.owns_client:
            return
        if self.hud is not None:
            self.check_contract(HUD_CONFIG)
        self.client = self.client_factory(validation_only=True)
        self.client.start()
        if self.hud is not None:
            self.client.freeze_settings(self.placement(False))

    def submit(self, processed):
        # Order matters for the reported reason: contract, then transport error, then payload.
        if self.hud is not None:
            self.check_contract(HUD_CONFIG)
        if self.owns_client and self.client.error:
            raise RuntimeError(self.client.error)
        if self.hud is not None and (processed.hud is None or processed.hud.config != self.hud or
                                     processed.image.shape != (self.hud.height_px, self.hud.width_px, 3)):
            raise ValueError("hud_payload_contract_mismatch")
        if self.owns_client:
            self.client.set_settings(self.placement(True))
        if processed.expires_ns is None:
            self.client.submit_bgr(processed.image)
        else:
            self.client.submit_bgr(processed.image, expires_ns=processed.expires_ns)

    def clear(self):
        if self.owns_client and self.client is not None:
            self.client.set_settings(self.placement(False))

    def close(self):
        if not self.owns_client or self.client is None:
            return True
        report = self.report = self.client.close()
        native = report.get("native_report") or {}
        return (bridge_closed_cleanly(report) and report.get("validation_only") is True and
                native.get("mode") == "--validate-stdin" and native.get("runtime_initialized") is False)
