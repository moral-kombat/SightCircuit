"""The observed-frame client (work-order item L8): the Python end of the bridge's fixed-HUD frame path.

``ObservedFrameClient`` sends the bridge observed frames (opcode 6), the
visibility requests that take the tile down and bring it back, and the
keepalive; it never sends a settings packet. It starts the validation bridge,
which takes opcode 6 under the fixed HUD's rules and displays nothing. It
would start the bridge's ``--fixed-hud`` mode, which is real output, only if
``fixed_hud_output_permitted`` said so. That function permits only a client
that carries a one-run scope (``runtime.scope.RunScope``, work-order item
L10) whose record verifies in full; every other start is refused through the
``LIVE_HUD_OUTPUT`` gate before anything is launched, and the gate itself
stays closed. The transport (bounded pipe writes, the reader thread, the stop and
its escalation) is ``overlay.OverlayClient``'s, which itself still starts
only the validation and replay streams.
"""
import math
import os
import subprocess
import threading
import time

from .gates import LIVE_HUD_OUTPUT
from .hud.contract import BRIDGE_READY_WAIT_SECONDS, OPCODE_STOP
from .overlay import (DEFAULT_BRIDGE, OverlayClient, bridge_closed_cleanly, observed_frame_packet, packet,
                      visibility_packet)
from .runtime.scope import HUD_OUTPUT, RunScope


FIXED_HUD_MODE = "--fixed-hud"
VALIDATION_MODE = "--validate-stdin"
FIXED_HUD_REFUSAL = "fixed_hud_output_not_approved"
# The bridge ends after ten seconds without a complete packet and takes no settings heartbeat in ``--fixed-hud``, so
# the client repeats its last visibility request once this long has passed without a packet. A cloud choice.
KEEPALIVE_SECONDS = 2.0
KEEPALIVE_MAX_SECONDS = 5.0  # Half the bridge's ten-second input timeout: a longer interval is refused.
MAX_EXPIRY_EVENTS = 1024  # ``frame_expired`` lines kept; later ones are counted in ``expiry_events_dropped``.
# What the client keeps of a ``frame_expired`` line: times, identities, flags and the bridge's outcome word.
EXPIRY_EVENT_FIELDS = ("deadline_ns", "observed_ns", "validation_only", "content_current", "frame_id",
                       "frame_observed_ns", "outcome", "hide_called_ns", "hide_returned_ns", "hide_ok", "hide_error",
                       "api_visible_after_hide", "texture_cleared")
FIRST_FRAME_LINES = ("frame_validated", "frame_submitted")
# The bridge's answer to each observed frame: presented (its calls returned) or refused, with the reason.
PRESENTED_LINE, REFUSED_LINE = "frame_presented", "frame_refused"
REFUSAL_REASONS = ("stale", "expired_on_arrival", "retransmission", "expired_before_show")
MAX_ACKNOWLEDGEMENTS = 4096  # Presented and refused lines kept, each; a run is at most 900 frames.
# The bridge's lines handed to the listener as they are read (``bridge_line``): what was presented, refused,
# expired, hidden or shown, in the bridge's own order and on its clock.
LISTENER_LINES = (PRESENTED_LINE, REFUSED_LINE, "frame_expired", "hide_returned", "show_returned")
# Counters of the bridge's final report that the client's own report repeats (None when the bridge wrote none).
BRIDGE_COUNTS = ("observed_frames_received", "frames_received", "frames_submitted", "frames_retransmitted",
                 "stale_frames_dropped", "expired_frames", "frames_expired_in_upload", "settings_received",
                 "visibility_requests_received", "visibility_events_missing")


def fixed_hud_output_permitted(client):
    """Whether ``client`` may start the bridge in ``--fixed-hud``, real output: only under a one-run scope record.

    This is the one place that decides it. ``ObservedFrameClient.check_start``
    hands the answer to the ``LIVE_HUD_OUTPUT`` gate's ``require``, which
    refuses everything but the literal ``True``. The answer is ``True`` only
    for an ``ObservedFrameClient`` whose ``scope`` is a ``RunScope`` (that
    exact type) which permits real HUD output: its record verified in full
    against what is on disk and its run id was consumed, all before this
    returns and so before anything is launched (``runtime/scope.py`` lists
    the checks). A scope permits this once, so a second start under the same
    record is refused. No argument, environment variable, configuration value
    or bridge line changes the answer. The gate is not open: this is the
    diagnostic tier of owner decision 15, one record for one displayed step,
    a procedural control against mistakes and not a cryptographic one.
    """
    scope = getattr(client, "scope", None)
    if not isinstance(client, ObservedFrameClient) or type(scope) is not RunScope:
        return False
    return scope.permit(HUD_OUTPUT) is True


class ObservedFrameClient(OverlayClient):
    """The Python end of the bridge's observed-frame path (opcode 6): the fixed HUD's only sender.

    It starts the bridge in validation (``--validate-stdin``, which takes
    opcode 6 under the fixed HUD's rules and displays nothing) or, with
    ``validation_only`` false, in ``--fixed-hud``. The second is real output
    and is refused before anything is launched (``check_start``, through the
    ``LIVE_HUD_OUTPUT`` gate) unless ``fixed_hud_output_permitted``, the single
    place that decides, finds a one-run scope record that verifies on
    ``scope``. ``scope`` is a ``runtime.scope.RunScope`` or None; a
    validation client never consults it.

    What it sends, all from one writer thread, so the order on the pipe is
    the order of the calls:

    * observed frames (``submit_observed``): the identity, the observation
      time and the deadline the caller gives, never a time of this client's.
      One frame waits at most; a newer one replaces it (``frames_replaced``),
      and a frame whose deadline has passed by the time the writer reaches it
      is not sent (``frames_expired_before_send``).
    * visibility requests (opcode 5): a hide when the caller takes the tile
      down (``take_down``), and a show right after the next frame, because the
      bridge keeps a hidden tile hidden until a show request. Each carries a
      rising cycle number and the bridge's lines for it are kept as
      ``OverlayClient`` keeps them.
    * the keepalive: after ``keepalive_seconds`` without a packet the last
      visibility request is repeated (a show until the first ``take_down``).
      Repeating it changes nothing on the display: a show with nothing
      presented is answered ``no_image``, and while the tile is taken down the
      keepalive is a hide, so it cannot bring back a tile that has not yet
      reached its deadline.
    * on ``end`` and on ``close``, a hide request unless the tile is already
      taken down; ``close`` then sends the stop packet. ``end`` is final:
      under the same lock as the queue, it drops a waiting frame and latches
      the client, so no frame is accepted and no show request is written
      afterwards, whichever thread asks. The bridge's own cleanup hides,
      clears and destroys the overlay whether or not either packet arrives.

    The hide is a packet on the same pipe as the frames. A frame the writer
    is already writing when the end comes is finished first, and the bridge,
    which is one thread, uploads and may show it before it reads the hide:
    the end-of-run hide can therefore trail the end by one frame's write and
    upload. ``final_hide`` records when the hide was written so a run can
    measure it against the bridge's ``hide_returned`` line.

    It never sends a settings packet, a frame without a deadline or an
    expiring frame of the validation layout: the inherited calls that would
    are refused (``observed_frame_client_refuses_<call>``).

    What it reads: the ready line (which must announce observed frames, and
    ``fixed_hud`` in real output), the bridge's first-frame line (its only
    per-frame acknowledgement, kept with the time it was read), every
    ``frame_expired`` line (``expiry_events``, the first ``MAX_EXPIRY_EVENTS``
    with the fields of ``EXPIRY_EVENT_FIELDS``), the visibility lines and the
    final report, and the bridge's answer to each frame: ``frame_presented``
    (kept in ``presented``, by identity, with the time the bridge's calls
    returned and whether the tile was asked to be up) or ``frame_refused``
    with its reason (``refused``). A frame with neither line was never
    acknowledged. ``listener(event, **fields)``, when set, is called on the
    reader thread with ``bridge_line`` (``line``: one of ``LISTENER_LINES``,
    as read, in the bridge's order) and on the
    writer thread as each packet has been written: ``frame_sent`` (``frame_id``,
    ``observed_ns``, ``expires_ns``, ``sent_ns``, ``hidden``: whether the tile
    was taken down when the frame went, so that only the show brings it up,
    ``token``), ``taken_down`` (``sent_ns``) and ``shown`` (``sent_ns``); and,
    on the writer's or the caller's thread, ``frame_not_sent`` (``frame_id``,
    ``reason``, ``token``). Times are ``time.monotonic_ns()``. The listener
    must not call back into the client.
    """

    def __init__(self, binary=DEFAULT_BRIDGE, *, validation_only=True, env=None, keepalive_seconds=KEEPALIVE_SECONDS,
                 listener=None, scope=None):
        if type(validation_only) is not bool:
            raise ValueError("observed_frame_client_mode_invalid")
        if (not isinstance(keepalive_seconds, (int, float)) or not math.isfinite(keepalive_seconds)
                or not 0 < keepalive_seconds <= KEEPALIVE_MAX_SECONDS):
            raise ValueError("observed_frame_keepalive_invalid")
        super().__init__(binary, validation_only=validation_only, env=env)
        self.keepalive_seconds, self.listener = float(keepalive_seconds), listener
        self.scope = scope  # Read only by ``fixed_hud_output_permitted`` and the stage's camera-input check.
        self._output_permitted = None  # The permission check's answer once it permitted; set nowhere else.
        self._started_real_output = False
        self.hidden = False  # The last visibility request written was a hide: the tile is taken down.
        self._want_hidden = False  # What the caller asked for last, under ``condition``; the writer follows it.
        self.final = False  # ``end`` was called: nothing is accepted and nothing is shown any more.
        self.final_hide = None  # {"cycle", "sent_ns"} of the hide written at or after ``end``; None if none was.
        self.presented, self.refused = {}, {}  # frame_id -> the bridge's line, the first MAX_ACKNOWLEDGEMENTS each.
        self.presented_count = self.refused_count = 0
        self.refused_reasons = dict.fromkeys(REFUSAL_REASONS, 0)
        self._pending_observed = None  # (packet, frame_id, observed_ns, expires_ns, token)
        self._last_frame_id = None
        self._cycle = 0
        self.frames_submitted = self.frames_replaced = self.frames_withdrawn = self.frames_expired_before_send = 0
        self.take_downs = self.shows = self.keepalives = 0
        self.first_frame = None  # The bridge's first-frame line, with ``read_ns``.
        self.frames_sent_before_first_frame_line = None
        self.expiry_events, self.expiry_event_count = [], 0
        self.line_counts = {}
        self._close_lock = threading.Lock()
        self._closed_report = None

    # -- the gate ------------------------------------------------------------------------------------------------

    def bridge_mode(self):
        return VALIDATION_MODE if self.validation_only else FIXED_HUD_MODE

    def check_start(self):
        """Refuse real output: validation starts freely, ``--fixed-hud`` only if the one permission check says so.

        A scope grants real output once and consumes its run id, so a
        permitted answer is kept for this client: ``start`` after a permitted
        ``check_start`` (the stage's order) is the same start. A refusal is
        never kept, and nothing else sets the kept answer.
        """
        if self.validation_only is not True:
            if self._output_permitted is not True:
                self._output_permitted = fixed_hud_output_permitted(self)
            LIVE_HUD_OUTPUT.require(self._output_permitted, FIXED_HUD_REFUSAL)

    def start(self):
        """Start the bridge in this client's mode, after the gate; otherwise as ``OverlayClient.start``."""
        self.check_start()
        if self.validation_only is not True and self._started_real_output:
            raise ValueError("observed_frame_client_already_started")  # One permitted start, one bridge.
        self._started_real_output = self.validation_only is not True
        if not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            raise RuntimeError("Overlay bridge missing; run bash scripts/build_overlay_bridge.sh")
        self.process = subprocess.Popen([str(self.binary), self.bridge_mode()], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, env=self.env, bufsize=0)
        os.set_blocking(self.process.stdin.fileno(), False)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        if not self.ready.wait(BRIDGE_READY_WAIT_SECONDS) or not self.acknowledged or self.error:
            error = self.error or f"Overlay bridge did not initialize within {BRIDGE_READY_WAIT_SECONDS} seconds"
            self.close()
            raise RuntimeError(error)
        self.writer = threading.Thread(target=self._write, daemon=True)
        self.writer.start()
        return self

    # -- what the bridge writes ----------------------------------------------------------------------------------

    def _line(self, message):
        event = message.get("event")
        if isinstance(event, str) and len(event) <= 64:
            self.line_counts[event] = self.line_counts.get(event, 0) + 1
        if event == "ready":
            if message.get("observed_frames_supported") is not True or message.get("frame_expiry_supported") is not True:
                raise RuntimeError("overlay_bridge_lacks_observed_frames")
            if (message.get("fixed_hud") is True) != (not self.validation_only):
                raise RuntimeError("overlay_bridge_fixed_hud_mode_mismatch")
        elif event in FIRST_FRAME_LINES and self.first_frame is None:
            self.frames_sent_before_first_frame_line = self.frames_sent
            self.first_frame = {"event": event, "width": message.get("width"), "height": message.get("height"),
                                "read_ns": time.monotonic_ns()}
        elif event == "frame_expired":
            self.expiry_event_count += 1
            if len(self.expiry_events) < MAX_EXPIRY_EVENTS:
                self.expiry_events.append({name: message[name] for name in EXPIRY_EVENT_FIELDS if name in message})
        elif event in (PRESENTED_LINE, REFUSED_LINE):
            frame_id = message.get("frame_id")
            if type(frame_id) is not int or frame_id < 0:
                raise RuntimeError("overlay_frame_line_invalid")
            if event == PRESENTED_LINE:
                at = message.get("presented_ns")
                if type(at) is not int or at < 0 or type(message.get("visible")) is not bool:
                    raise RuntimeError("overlay_frame_line_invalid")
                self.presented_count += 1
                if len(self.presented) < MAX_ACKNOWLEDGEMENTS:
                    self.presented[frame_id] = {"presented_ns": at, "visible": message["visible"],
                                                "read_ns": time.monotonic_ns()}
            else:
                reason = message.get("reason")
                if reason not in REFUSAL_REASONS:
                    raise RuntimeError("overlay_frame_line_invalid")
                self.refused_count += 1
                self.refused_reasons[reason] += 1
                if len(self.refused) < MAX_ACKNOWLEDGEMENTS:
                    self.refused[frame_id] = reason
        if event in LISTENER_LINES:
            self._notify("bridge_line", line=message)

    @property
    def expiry_events_dropped(self):
        return self.expiry_event_count - len(self.expiry_events)

    # -- what the caller sends -----------------------------------------------------------------------------------

    def _running(self):
        return self.writer is not None and self.writer.is_alive() and not self.stopping.is_set()

    def submit_observed(self, frame_id, observed_ns, expires_ns, width, height, rgba, *, token=None):
        """Queue one observed frame for the writer; True when queued, False once ended or no longer running.

        ``observed_ns`` is the source frame's observation time and
        ``expires_ns`` its deadline, at most ``MAX_DISPLAY_AGE_NS`` later
        (``observed_frame_packet`` refuses anything else, as the bridge
        would). ``frame_id`` must be higher than every identity queued before:
        this client never retransmits and never reuses one. ``token`` comes
        back unchanged to the listener.
        """
        data = observed_frame_packet(frame_id, observed_ns, expires_ns, width, height, rgba)
        with self.condition:
            if self.final or not self._running():
                return False
            if self._last_frame_id is not None and frame_id <= self._last_frame_id:
                raise ValueError("observed_frame_identity_not_rising")
            self._last_frame_id = frame_id
            if self._pending_observed is not None:
                self.frames_replaced += 1
                self._notify("frame_not_sent", frame_id=self._pending_observed[1], reason="replaced_before_send",
                             token=self._pending_observed[4])
            self._pending_observed = (data, frame_id, observed_ns, expires_ns, token)
            self._want_hidden = False
            self.frames_submitted += 1
            self.condition.notify()
        return True

    def take_down(self):
        """Take the tile down now: drop a frame still waiting and queue a hide request; True when queued or already down.

        The bridge then keeps the tile hidden, whatever its deadline, until
        the show request that follows the next frame. Safe from any thread and
        after the client has stopped (False then: the bridge's own cleanup
        hides, clears and destroys the overlay).
        """
        with self.condition:
            if self._pending_observed is not None:
                self.frames_withdrawn += 1
                self._notify("frame_not_sent", frame_id=self._pending_observed[1], reason="taken_down_before_send",
                             token=self._pending_observed[4])
                self._pending_observed = None
            if not self._running():
                return False
            self._want_hidden = True
            self.condition.notify()
        return True

    def end(self):
        """The final take-down: as ``take_down``, and from now on no frame is accepted and no show is written.

        The latch is set under the queue's lock, so a ``submit_observed``
        racing it is either dropped here or refused there; it can never undo
        the take-down. Safe from any thread, any number of times.
        """
        with self.condition:
            self.final = True
        return self.take_down()

    def _notify(self, event, **fields):
        if self.listener is not None:
            self.listener(event, **fields)

    def _visibility(self, visible):
        self._cycle = (self._cycle + 1) % 2 ** 32
        self._send(visibility_packet(self._cycle, visible))
        self.visibility_requests += 1
        self.hidden = not visible
        return time.monotonic_ns()

    def _write(self):
        try:
            last_packet = time.monotonic()
            while True:
                with self.condition:
                    if (self._pending_observed is None and not (self._want_hidden and not self.hidden)
                            and not self.stopping.is_set()):
                        self.condition.wait(timeout=max(0.0, last_packet + self.keepalive_seconds - time.monotonic()))
                    frame, self._pending_observed = self._pending_observed, None
                    hide = self._want_hidden
                # A hide queued with a frame was asked for first (``take_down`` drops the frame that waited), so
                # the order on the pipe is the hide, the frame, then the show that lets the frame's tile up. At
                # the end the hide goes out before the stop packet: every run ends with the tile taken down by name.
                if hide and not self.hidden:
                    sent_ns = self._visibility(False)
                    self.take_downs += 1
                    last_packet = time.monotonic()
                    if self.final and self.final_hide is None:
                        self.final_hide = {"cycle": self._cycle, "sent_ns": sent_ns}
                    self._notify("taken_down", sent_ns=sent_ns)
                if self.stopping.is_set():
                    if frame is not None:
                        self._notify("frame_not_sent", frame_id=frame[1], reason="stopped_before_send", token=frame[4])
                    self._send(packet(OPCODE_STOP))
                    break
                if frame is not None:
                    data, frame_id, observed_ns, expires_ns, token = frame
                    if time.monotonic_ns() >= expires_ns:  # Never sent: the bridge would refuse it on arrival.
                        self.frames_expired_before_send += 1
                        self._notify("frame_not_sent", frame_id=frame_id, reason="expired_before_send", token=token)
                    else:
                        self._send(data)
                        sent_ns = time.monotonic_ns()
                        self.frames_sent += 1
                        last_packet = time.monotonic()
                        self._notify("frame_sent", frame_id=frame_id, observed_ns=observed_ns, expires_ns=expires_ns,
                                     sent_ns=sent_ns, hidden=self.hidden, token=token)
                        with self.condition:
                            show = self.hidden and not self._want_hidden  # Not when it was taken down meanwhile.
                        if show:
                            sent_ns = self._visibility(True)
                            self.shows += 1
                            self._notify("shown", sent_ns=sent_ns)
                if time.monotonic() - last_packet >= self.keepalive_seconds:
                    self._visibility(not self.hidden)  # The request already in force, repeated: nothing changes.
                    self.keepalives += 1
                    last_packet = time.monotonic()
        except Exception as error:  # Anything, a listener's failure included: the client fails and says so.
            if not self.stopping.is_set():
                self._fail(error if str(error) else type(error).__name__)
        finally:
            self.process.stdin.close()

    # -- what an observed-frame client never sends ---------------------------------------------------------------

    def _refuse(self, call):
        raise ValueError(f"observed_frame_client_refuses_{call}")

    def set_settings(self, settings):
        self._refuse("settings")

    def freeze_settings(self, settings):
        self._refuse("settings")

    def request_visibility(self, cycle, visible, *, settings=None):
        self._refuse("visibility_cycles")

    def submit_frame(self, width, height, rgba, *, expires_ns=None):
        self._refuse("frames_without_observation")

    # -- the end -------------------------------------------------------------------------------------------------

    def close(self):
        """Stop the bridge once (any thread; later calls return the first report) and report the transport.

        Beside ``OverlayClient.close``'s fields: the mode, this side's counts,
        the bridge's own (``bridge_counts``), and ``cleanup_confirmed`` only
        when the bridge closed cleanly in the mode this client started: a
        validation bridge that never initialised a runtime, or a fixed-HUD
        bridge whose hide, texture clear and destroy all returned. A client
        that was never started has nothing to clean up.
        """
        with self._close_lock:
            if self._closed_report is None:
                started = self.process is not None
                self.end()  # The writer sends the hide, if the tile is not down already, then the stop.
                report = super().close()
                native = report.get("native_report") or {}
                if self.validation_only:
                    mode_ok = native.get("mode") == VALIDATION_MODE and native.get("runtime_initialized") is False
                else:
                    mode_ok = (native.get("mode") == FIXED_HUD_MODE and native.get("hide_returned_ok") is True and
                               native.get("clear_texture_returned_ok") is True and native.get("destroy_returned_ok") is True)
                report.update(
                    started=started, mode=self.bridge_mode(), frames_submitted=self.frames_submitted,
                    frames_replaced=self.frames_replaced, frames_withdrawn=self.frames_withdrawn,
                    frames_expired_before_send=self.frames_expired_before_send, frames_retransmitted=0,
                    take_downs=self.take_downs, shows=self.shows, keepalives=self.keepalives,
                    visibility_requests=self.visibility_requests, settings_sent=0,
                    frames_presented=self.presented_count, frames_refused=self.refused_count,
                    refused_reasons=dict(self.refused_reasons),
                    expiry_events=self.expiry_event_count, expiry_events_dropped=self.expiry_events_dropped,
                    bridge_counts={name: native.get(name) for name in BRIDGE_COUNTS},
                    cleanup_confirmed=(not started and self.error is None) or (bridge_closed_cleanly(report) and mode_ok))
                self._closed_report = report
            return self._closed_report
