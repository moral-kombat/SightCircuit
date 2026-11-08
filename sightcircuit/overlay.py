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
                           OPCODE_VISIBILITY, PACKET_HEADER_FORMAT, PACKET_MAGIC, RGBA_BYTES, SETTINGS_FORMAT,
                           VISIBILITY_EVENT_BOUND_NS, VISIBILITY_FORMAT, VISIBILITY_HIDE_LINES, VISIBILITY_LINES,
                           VISIBILITY_MISSING_LINE, VISIBILITY_SHOW_LINES)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BRIDGE = ROOT / "artifacts/overlay-bridge/sightcircuit-overlay-bridge"
# (name, low, high); the native bridge takes these from the generated tools/hud_contract.hpp.
PANEL_LIMITS = (("width", .25, 1.5), ("distance", .5, 3), ("x", -1, 1), ("y", -1, 1), ("opacity", .2, 1))

# The Python side's deadline on one visibility request's terminal line (fallback item F2), in nanoseconds of
# time.monotonic_ns(), counted from the moment the request is queued here. The bridge reports a request whose
# runtime event has not been polled within VISIBILITY_EVENT_BOUND_NS as missing on its next input poll, which
# returns at most BRIDGE_INPUT_POLL_NS later (the sensing unit's poll(2) timeout, pinned against its source by
# tests/test_visibility_cycle.py); the margin covers this side's pipe write of the request, the bridge's decode,
# the line's write, the reader thread's readline and the waiter's wake-up, each far under a millisecond when
# uncontended. 320 ms in all: a terminal line that has not arrived by then is a failed request (fail closed), and
# the whole stays under the fastest costed cycle (500 ms at two per second) with room for the window. These are
# cloud choices, unmeasured on the Frame; the hidden-event delivery measurement (F3) replaces the bound they
# are built on.
BRIDGE_INPUT_POLL_NS = 20_000_000
VISIBILITY_DEADLINE_MARGIN_NS = 50_000_000
VISIBILITY_TERMINAL_DEADLINE_NS = VISIBILITY_EVENT_BOUND_NS + BRIDGE_INPUT_POLL_NS + VISIBILITY_DEADLINE_MARGIN_NS
# Cycles whose lines the client keeps; past it the oldest cycle's lines are dropped and counted.
VISIBILITY_HISTORY_CYCLES = 1024
# Why a request did not confirm, beside the bridge's own reason on a returned line with ok false and the name
# of the missing line itself.
VISIBILITY_DEADLINE_REASON = "visibility_deadline"
VISIBILITY_BRIDGE_ENDED_REASON = "overlay_bridge_ended"
VISIBILITY_ORDER_REASON = "visibility_lines_out_of_order"


def packet(kind, payload=b""):
    return struct.pack(PACKET_HEADER_FORMAT, PACKET_MAGIC, kind, len(payload)) + payload


def visibility_packet(cycle, visible):
    """The show (``True``) or hide request of fallback cycle ``cycle`` (``OPCODE_VISIBILITY``).

    ``OverlayClient.request_visibility`` sends it (work item F2). The bridge
    answers with the timestamped visibility lines of
    ``sightcircuit.hud.contract.VISIBILITY_LINES``, each carrying ``cycle``.
    """
    if type(cycle) is not int or not 0 <= cycle < 2 ** 32:
        raise ValueError("visibility_cycle_out_of_range")
    if type(visible) is not bool:
        raise ValueError("Panel visibility must be boolean")
    return packet(OPCODE_VISIBILITY, struct.pack(VISIBILITY_FORMAT, cycle, int(visible)))


def _zero(value):
    return type(value) is int and value == 0


def _cycle_number(cycle):
    if type(cycle) is not int or not 0 <= cycle < 2 ** 32:
        raise ValueError("visibility_cycle_out_of_range")
    return cycle


@dataclass(frozen=True)
class VisibilityRequest:
    """One queued show (``visible``) or hide request of fallback cycle ``cycle``.

    ``requested_ns`` is ``time.monotonic_ns()`` when the client queued it, and
    ``deadline_ns`` the default deadline on its terminal line
    (``VISIBILITY_TERMINAL_DEADLINE_NS`` later).
    """
    cycle: int
    visible: bool
    requested_ns: int

    @property
    def deadline_ns(self):
        return self.requested_ns + VISIBILITY_TERMINAL_DEADLINE_NS


@dataclass(frozen=True)
class VisibilityOutcome:
    """How one visibility request ended, read from the cycle's lines (``transition_outcome``).

    ``confirmed`` is true only when the runtime's event line (``overlay_hidden``
    for a hide, ``overlay_shown`` for a show) followed the request's own
    requested and returned-ok lines; ``observed_ns`` is then the event's time in
    the bridge's ``CLOCK_MONOTONIC`` nanoseconds, the clock of
    ``Frame.observed_ns`` on the same host, and the admission window of a hide
    opens from it. Otherwise ``reason`` says why not: the returned line's
    ``reason`` (``no_image``, or the call's error), the missing line's name,
    ``visibility_deadline`` when the deadline passed first,
    ``overlay_bridge_ended`` or the client's error when the bridge ended first,
    or ``visibility_lines_out_of_order``. ``terminal`` names the line that ended
    the request, or is None when no line did. ``lines`` are the request's own
    lines in arrival order. A hide that is not confirmed opens no window.
    """
    cycle: int
    visible: bool
    confirmed: bool
    terminal: str | None
    reason: str | None
    request_ns: int | None
    returned_ns: int | None
    observed_ns: int | None
    waited_ns: int | None
    call: str | None
    lines: tuple


def _timestamp(line, field):
    value = line.get(field) if line is not None else None
    return value if type(value) is int and value >= 0 else None


def transition_outcome(cycle, visible, lines, ended=None):
    """The outcome of cycle ``cycle``'s show (``visible`` true) or hide from the cycle's lines in arrival order.

    None while the request is still pending, unless ``ended`` names why no
    further line can come (the deadline, the bridge ending); then a failed
    outcome. The bridge writes a hide's lines before the cycle's show lines and
    exactly one terminal line per request, so a hide's lines end at the first
    show line and a show's begin at ``show_requested``; a missing line is the
    request's whose lines it falls among. The terminal line is the first of the
    event line, the missing line and a returned line with ``ok`` false. A
    returned line with ``ok`` false ends the request here even though the
    bridge still awaits (and later reports missing) its event.
    """
    requested, returned, event = VISIBILITY_SHOW_LINES if visible else VISIBILITY_HIDE_LINES
    other = VISIBILITY_HIDE_LINES if visible else VISIBILITY_SHOW_LINES
    own, terminal, requested_line, returned_line = [], None, None, None
    started = not visible
    for line in lines:
        name = line.get("event")
        if not started:
            started = name == requested
            if not started:
                continue
        if name in other:
            if visible:
                continue  # a hide line after show_requested is not the show's
            break  # the hide's lines end where the show's begin
        own.append(line)
        if name == requested:
            requested_line = line
        elif name == returned:
            returned_line = line
        if name == event or name == VISIBILITY_MISSING_LINE or (name == returned and line.get("ok") is not True):
            terminal = line
            break
    request_ns, returned_ns = _timestamp(requested_line, "request_ns"), _timestamp(returned_line, "returned_ns")
    call = returned_line.get("call") if returned_line is not None else None
    if terminal is None:
        if ended is None:
            return None
        return VisibilityOutcome(cycle, visible, False, None, str(ended), request_ns, returned_ns, None, None, call,
                                 tuple(own))
    name, observed_ns, waited_ns = terminal["event"], None, None
    if name == event:
        observed_ns = _timestamp(terminal, "observed_ns")
        ordered = requested_line is not None and returned_line is not None and returned_line.get("ok") is True
        confirmed = ordered and observed_ns is not None
        reason = None if confirmed else VISIBILITY_ORDER_REASON
        if not confirmed:
            observed_ns = None
    elif name == VISIBILITY_MISSING_LINE:
        confirmed, reason, waited_ns = False, name, _timestamp(terminal, "waited_ns")
    else:
        confirmed, reason = False, str(terminal.get("reason") or "visibility_call_failed")
    return VisibilityOutcome(cycle, visible, confirmed, name, reason, request_ns, returned_ns, observed_ns, waited_ns,
                             call, tuple(own))


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
        # The visibility cycle (F2): the lines the bridge wrote, by cycle, in arrival order, and the waiters'
        # condition; the requests queued for the writer live under ``condition`` with the frame and settings.
        self.visibility_condition = threading.Condition()
        self.visibility_lines = {}
        self.visibility_cycles_dropped = 0
        self.pending_visibility = []
        self.visibility_requests = 0
        self.forced_termination = False
        self.stop_requested = False
        self.process = self.reader = self.writer = None

    def _fail(self, error):
        with self.condition:
            if not self.error:
                self.error = str(error)
        self._wake_visibility_waiters()

    def _wake_visibility_waiters(self):
        with self.visibility_condition:
            self.visibility_condition.notify_all()

    def _keep_visibility_line(self, message):
        """Queue one of the bridge's visibility lines under its cycle; every line is kept, oldest cycles first out."""
        cycle = message.get("cycle")
        if type(cycle) is not int or not 0 <= cycle < 2 ** 32:
            raise RuntimeError("overlay_visibility_line_invalid")
        with self.visibility_condition:
            self.visibility_lines.setdefault(cycle, []).append(message)
            while len(self.visibility_lines) > VISIBILITY_HISTORY_CYCLES:
                del self.visibility_lines[next(iter(self.visibility_lines))]
                self.visibility_cycles_dropped += 1
            self.visibility_condition.notify_all()

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
                if event in VISIBILITY_LINES:
                    self._keep_visibility_line(message)
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
            self._wake_visibility_waiters()

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
                    if (self.pending_frame is None and not self.pending_visibility
                            and self.settings == previous_settings and not self.stopping.is_set()):
                        self.condition.wait(timeout=1)
                    frame, self.pending_frame = self.pending_frame, None
                    requests, self.pending_visibility = self.pending_visibility, []
                    settings = self.settings
                if self.stopping.is_set():
                    self._send(packet(OPCODE_STOP))
                    break
                # Without a cycle request the order is the settings, then the frame, as before F2: an
                # interactive hide lands before the frame it shares an iteration with. With one, the frame, then
                # the requests, then the settings: a tile submitted before a show request is presented before it,
                # and the settings packet, which the bridge applies as a show or hide call too, follows the
                # request so that the request's call is the transition the runtime's event answers and the
                # heartbeat then repeats the cycle's state instead of undoing it.
                send_settings = settings != previous_settings or time.monotonic() - last_heartbeat >= 1
                if send_settings and not requests:
                    self._send(settings.encode())
                    previous_settings, last_heartbeat = settings, time.monotonic()
                if frame:
                    self._send(frame)
                    self.frames_sent += 1
                for data in requests:
                    self._send(data)
                if send_settings and requests:
                    self._send(settings.encode())
                    previous_settings, last_heartbeat = settings, time.monotonic()
        except (OSError, RuntimeError, ValueError) as error:
            if not self.stopping.is_set():
                self._fail(error)
        finally:
            self.process.stdin.close()

    def _check_settings(self, settings):
        """Encodable, and the frozen placement when one is pinned (only visibility may differ); under ``condition``."""
        if self._fixed_settings is not None and replace(settings, visible=False) != self._fixed_settings:
            raise ValueError("overlay_settings_frozen")

    def set_settings(self, settings):
        settings.encode()
        with self.condition:
            self._check_settings(settings)
            self.settings = settings
            self.condition.notify()

    def request_visibility(self, cycle, visible, *, settings=None):
        """Queue the show (``visible``) or hide request of fallback cycle ``cycle`` for the writer.

        With ``settings``, the client's settings change to them in the same
        step and the writer sends them right after the request, so the
        heartbeat repeats the cycle's visibility instead of undoing it (the
        bridge applies every settings packet as a show or hide call). Returns
        the ``VisibilityRequest`` to await. Raises when the client is not
        running; a request queued before a transport failure ends with that
        failure as its outcome's reason.
        """
        data = visibility_packet(cycle, visible)
        if settings is not None:
            settings.encode()
        with self.condition:
            if self.writer is None or not self.writer.is_alive() or self.stopping.is_set():
                raise RuntimeError("overlay_client_not_running")
            if settings is not None:
                self._check_settings(settings)
                self.settings = settings
            request = VisibilityRequest(cycle, visible, time.monotonic_ns())
            self.pending_visibility.append(data)
            self.visibility_requests += 1
            self.condition.notify()
        return request

    def cycle_lines(self, cycle):
        """Every visibility line the bridge wrote for ``cycle``, in arrival order."""
        with self.visibility_condition:
            return tuple(self.visibility_lines.get(_cycle_number(cycle), ()))

    def visibility_outcome(self, cycle, visible):
        """The outcome of ``cycle``'s show or hide so far, or None while it is pending; never blocks."""
        with self.visibility_condition:
            return transition_outcome(_cycle_number(cycle), visible, self.visibility_lines.get(cycle, ()))

    def await_visibility(self, request, *, deadline_ns=None):
        """Block until ``request`` has its terminal line, the deadline passes or the bridge ends; the outcome.

        ``deadline_ns`` is in ``time.monotonic_ns()`` and defaults to the
        request's (``VISIBILITY_TERMINAL_DEADLINE_NS`` after it was queued).
        The deadline passing or the bridge ending first is a failed request,
        never an exception: the caller sees a cycle without a confirmed
        transition and opens no window.
        """
        if deadline_ns is None:
            deadline_ns = request.deadline_ns
        if type(deadline_ns) is not int:
            raise ValueError("visibility_deadline_invalid")
        with self.visibility_condition:
            while True:
                lines = self.visibility_lines.get(request.cycle, ())
                outcome = transition_outcome(request.cycle, request.visible, lines)
                if outcome is not None:
                    return outcome
                if self.done.is_set() or self.error:
                    return transition_outcome(request.cycle, request.visible, lines,
                                              ended=self.error or VISIBILITY_BRIDGE_ENDED_REASON)
                remaining = deadline_ns - time.monotonic_ns()
                if remaining <= 0:
                    return transition_outcome(request.cycle, request.visible, lines, ended=VISIBILITY_DEADLINE_REASON)
                self.visibility_condition.wait(min(remaining / 1e9, .05))

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



@dataclass(frozen=True)
class FallbackCycle:
    """One fallback cycle on the sink: its number, then the hide's outcome and the show's as each is known.

    ``hidden_ns`` is the confirmed hide's ``observed_ns`` (the bridge's
    ``CLOCK_MONOTONIC``, the clock of ``Frame.observed_ns``), from which the
    admission window opens, or None: no confirmed hide, no window this cycle.
    """
    cycle: int
    hide: VisibilityOutcome | None = None
    show: VisibilityOutcome | None = None

    @property
    def hidden_ns(self):
        return self.hide.observed_ns if self.hide is not None and self.hide.confirmed else None

    @property
    def hidden(self):
        """True between the hide request and the show request: the panel is down for this cycle."""
        return self.show is None


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

    The fallback's visibility cycle (work item F2) is ``hide`` then ``show``
    on an owned client: each sends the cycle's request through
    ``OverlayClient.request_visibility`` and blocks on its terminal line, and
    the sink's ``cycle`` keeps both outcomes. While a cycle is hidden,
    ``submit`` leaves the panel down (the tile is presented by the show).
    """

    def __init__(self, client=None, *, hud=None, client_factory=None):
        if hud is not None and client is not None:
            raise ValueError("hud_sink_requires_owned_validation_client")
        self.client, self.hud = client, hud
        self.owns_client = client is None
        self.client_factory = client_factory or OverlayClient
        self.report = None
        self.cycle = None

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
            self.client.set_settings(self.placement(self.panel_visible))
        if processed.expires_ns is None:
            self.client.submit_bgr(processed.image)
        else:
            self.client.submit_bgr(processed.image, expires_ns=processed.expires_ns)

    def clear(self):
        if self.owns_client and self.client is not None:
            self.client.set_settings(self.placement(False))

    @property
    def panel_visible(self):
        """False while a fallback cycle is hidden; a submitted result is then presented by the cycle's show."""
        return self.cycle is None or not self.cycle.hidden

    def _cycle_client(self):
        if not self.owns_client or self.client is None:
            raise RuntimeError("hud_sink_cycle_requires_owned_client")
        if self.hud is not None:
            self.check_contract(HUD_CONFIG)
        if self.client.error:
            raise RuntimeError(self.client.error)
        return self.client

    def hide(self, cycle, *, deadline_ns=None):
        """Start fallback cycle ``cycle``: hide the panel and block for the bridge's terminal line; the outcome.

        Cycle numbers increase, and the previous cycle's show must have been
        requested. The request goes with the hidden placement, so the settings
        heartbeat keeps the panel down. The outcome is confirmed only by the
        runtime's ``overlay_hidden`` line, and ``cycle.hidden_ns`` then holds
        its time for the admission window; a missing line, a returned line with
        ``ok`` false, the deadline (``VISIBILITY_TERMINAL_DEADLINE_NS`` after the
        request unless ``deadline_ns`` is given) or the bridge ending is a failed
        cycle with no window, reported, never raised.
        """
        _cycle_number(cycle)
        client = self._cycle_client()
        if self.cycle is not None:
            if cycle <= self.cycle.cycle:
                raise ValueError("hud_cycle_not_increasing")
            if self.cycle.hidden:
                raise ValueError("hud_cycle_show_pending")
        # The cycle is recorded only once its request is queued: a client that stopped between the check above
        # and the queue raises here and leaves the sink's state as it was, so the next hide is still accepted.
        request = client.request_visibility(cycle, False, settings=self.placement(False))
        self.cycle = FallbackCycle(cycle)
        outcome = client.await_visibility(request, deadline_ns=deadline_ns)
        self.cycle = replace(self.cycle, hide=outcome)
        return outcome

    def show(self, cycle, closes_ns, *, deadline_ns=None):
        """End cycle ``cycle``: once ``closes_ns`` has passed, show the panel and block for the terminal line.

        ``closes_ns`` is the admission window's close in ``time.monotonic_ns()``
        (the current time when the hide was not confirmed and there is no
        window); it may not lie further ahead than ``MAX_DISPLAY_AGE_NS``. The
        request is sent only after it, so no admitted frame can carry the tile,
        and the bridge's ``request_ns`` is at or after it on the same clock.
        The request goes with the visible placement, so the heartbeat keeps the
        panel up. The outcome is confirmed only by ``overlay_shown``; a show
        with nothing presented returns ``ok`` false with reason ``no_image``.
        """
        client = self._cycle_client()
        if self.cycle is None or self.cycle.cycle != cycle or self.cycle.hide is None:
            raise ValueError("hud_cycle_not_hidden")
        if not self.cycle.hidden:
            raise ValueError("hud_cycle_already_shown")
        if type(closes_ns) is not int or closes_ns < 0:
            raise ValueError("hud_window_close_invalid")
        if closes_ns > time.monotonic_ns() + MAX_DISPLAY_AGE_NS:
            raise ValueError("hud_window_close_too_far_ahead")
        while (remaining := closes_ns - time.monotonic_ns()) > 0:
            time.sleep(remaining / 1e9)
        request = client.request_visibility(cycle, True, settings=self.placement(True))
        outcome = client.await_visibility(request, deadline_ns=deadline_ns)
        self.cycle = replace(self.cycle, show=outcome)
        return outcome

    def close(self):
        if not self.owns_client or self.client is None:
            return True
        report = self.report = self.client.close()
        native = report.get("native_report") or {}
        return (bridge_closed_cleanly(report) and report.get("validation_only") is True and
                native.get("mode") == "--validate-stdin" and native.get("runtime_initialized") is False)
