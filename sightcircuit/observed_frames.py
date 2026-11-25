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

A client may name the fixed layout it sends for (``layout``, work-order item
B3): one of ``hud.contract.FIXED_LAYOUTS``. The bridge is then launched with
``--layout <layout_id>``, must repeat the name on its ready line, and takes
frames of exactly that layout's size; the client refuses any other frame
before it is queued. A client without one is the strip's client, exactly as
before layouts existed. Naming a layout permits nothing. Since work-order
item B5 the scope record binds the layout, by id and by configuration
digest, and the one permission site compares it with the layout the client
is about to launch (``layout_bound``): a client is permitted real output
with a layout if and only if a record that verifies names that layout, so a
record for the strip never admits the box panel and a record for the panel
never admits the strip. There is no list of layouts allowed real output
here or anywhere else; the contract lists what the bridge can show and a
record says what one run may. A difference is refused by name
(``fixed_hud_layout_not_approved``) before the record's run id is consumed.
That is a property of this repository's callers and of the procedure, not
of the bridge binary, which takes ``--layout`` from whoever launches it
(docs/reference/box-panel.md, "Who may show it").
"""
import math
import os
import subprocess
import threading
import time

from .gates import LIVE_HUD_OUTPUT
from .hud.contract import BRIDGE_READY_WAIT_SECONDS, FIXED_LAYOUTS, HUD_CONFIG, OPCODE_STOP, RGBA_BYTES, config_digest
from .overlay import (DEFAULT_BRIDGE, OverlayClient, bridge_closed_cleanly, observed_frame_packet, packet,
                      visibility_packet)
from .runtime.manifest import BRIDGE as PINNED_BRIDGE
from .runtime.scope import HUD_OUTPUT, RunScope, pinned_binary


FIXED_HUD_MODE = "--fixed-hud"
VALIDATION_MODE = "--validate-stdin"
FIXED_HUD_REFUSAL = "fixed_hud_output_not_approved"
LAYOUT_ARGUMENT = "--layout"  # Follows the mode on the bridge's command line when the client names a layout.
# The permission site's name for one refusal: the layout to be launched is not the layout the run's record binds.
LAYOUT_REFUSAL = "fixed_hud_layout_not_approved"
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


def output_preconditions(client, layout=None):
    """What the output site checks before anything is consumed; True or False, and it changes nothing.

    ``client`` is an ``ObservedFrameClient`` in real output; its ``scope`` is
    a ``RunScope`` (that exact type); its bridge is the checkout's own, at
    the path the checkpoint manifest hashes; it carries a duration bound
    of its own (``max_output_seconds``, which it enforces by ending real
    output itself) no longer than the scope's seconds, and a client with no
    bound is refused; and ``layout``, the layout value the start is about to
    launch with (None is the strip), is the one the scope was asked for
    (``layout_bound``).
    """
    return _output_preconditions(client, getattr(client, "scope", None), layout)


def _output_preconditions(client, scope, layout):
    """``output_preconditions`` on ``scope``, the one object the caller read from the client."""
    if not isinstance(client, ObservedFrameClient) or type(scope) is not RunScope or client.validation_only is not False:
        return False
    bound, permitted = client.max_output_seconds, scope.seconds
    if type(bound) not in (int, float) or type(permitted) not in (int, float) or not 0 < bound <= permitted:
        return False
    return layout_bound(scope, layout) and pinned_binary(client.binary, PINNED_BRIDGE)


def layout_bound(scope, layout, granted=False):
    """Whether ``layout``, the value a start is about to launch with, is the layout ``scope`` binds; True or False.

    The comparison of work-order item B5, and all that is left of the
    stopgap it replaces: no list of layouts is consulted, only what this
    one run asked for and what its record says. ``layout`` is the client's
    layout value itself (None is the strip's client), the object the launch
    arguments are then built from. It must be the contract's own object, the
    very one the scope was asked for (identity, so a copy or an edited copy
    is another layout); and once the scope has read its record, the record's
    ``hud_config_id`` and ``hud_config_sha256`` must be that layout's id and
    the digest of its fields as this checkout computes it. It reads the
    record the scope already holds, consumes nothing and changes nothing,
    so it is asked before the scope is consulted (a difference then costs
    no run id) and again after it. With ``granted`` the answer is for a scope
    that has just permitted: it must be granted and hold the record it was
    granted on, and that record is what is compared; a scope that has read
    no record never satisfies it.
    """
    shown = HUD_CONFIG if layout is None else layout
    if type(scope) is not RunScope or not any(shown is known for known in FIXED_LAYOUTS) or scope.hud_config is not shown:
        return False
    record = scope.record
    if granted and (scope.status != "granted" or record is None):
        return False
    if record is None:  # Not read yet, or refused before it was accepted: the request is all there is to compare.
        return True
    return (type(record) is dict and record.get("hud_config_id") == shown.layout_id
            and record.get("hud_config_sha256") == config_digest(shown))


def fixed_hud_output_permitted(client, layout=None):
    """Whether ``client`` may start the bridge in ``--fixed-hud``, real output: only under a one-run scope record.

    This is the one place that decides it. ``require_real_output`` hands the
    answer to the ``LIVE_HUD_OUTPUT`` gate's ``require``, which refuses
    everything but the literal ``True``. ``layout`` is the layout value the
    start is about to launch with (None is the strip). The order is: this
    site's own preconditions (``output_preconditions``), among them that the
    layout is the one the scope was asked for; then every callable in the
    client's ``prerequisites``, which is how a stage has its camera site's
    preconditions checked before anything is consumed; and only then the
    scope, whose first answer verifies the record in full against what is on
    disk, compares each field the record binds with what the run asked for,
    the layout by id and digest among them, and consumes its run id last,
    all before this returns and so before anything is launched
    (``runtime/scope.py`` lists the checks). Then the record the scope now
    holds is compared with the layout once more (``layout_bound``). The
    client's ``scope`` is read once, and that one object is the scope whose
    request is judged, the scope that is asked and the scope whose record
    is compared, so a client that answers with different scopes from one
    read to the next gains nothing. The
    client is the use's holder: when it asks again, at ``start``, the scope
    checks the deadline and the bridge's digest once more and consumes
    nothing; any other client is refused. No argument, environment
    variable, configuration value or bridge line changes the answer. The
    gate is not open: this is the diagnostic tier of owner decision 15, one
    record for one displayed step, a procedural control against mistakes
    and not a cryptographic one.

    Why the layout is compared in this order. Everything that can be told
    without spending the record is told first: a client whose layout is not
    the scope's is refused by the preconditions with the record unread, and
    a scope whose layout is not its record's is refused by the scope's own
    step 3, before the clock, the revision, the manifest and the ledger. In
    both cases the run id is unspent and the same record still serves the
    run it was written for. The run id is spent only by a scope that
    verified in full; what is refused or fails after that (a start past the
    deadline, a rewritten bridge, a run that dies before its bridge) leaves
    it spent, and a refused or failed run revokes its scope either way
    (``ObservedFrameClient.withdraw``), so nothing starts under it later.
    """
    scope = getattr(client, "scope", None)  # Read once: the scope judged, the scope asked and the scope compared are one.
    if not _output_preconditions(client, scope, layout):
        return False
    try:
        if not all(prerequisite() is True for prerequisite in client.prerequisites):
            return False
    except Exception:
        return False
    return scope.permit(HUD_OUTPUT, client) is True and layout_bound(scope, layout, granted=True)


def output_refusal(client, layout):
    """The name the output site refuses under; it decides nothing and changes nothing.

    ``LAYOUT_REFUSAL`` when the client carries a scope and the layout it is
    about to launch with is not the one that scope and its record bind
    (``layout_bound``), so the operator is told which thing differed; else
    ``FIXED_HUD_REFUSAL``, which covers every other reason, a missing record
    included.
    """
    scope = getattr(client, "scope", None)
    return LAYOUT_REFUSAL if type(scope) is RunScope and not layout_bound(scope, layout) else FIXED_HUD_REFUSAL


def require_real_output(client, layout):
    """Refuse real output with ``layout`` unless the one permission check says so; the gate's one ``require``.

    ``layout`` is a value, read once by the caller: ``start`` hands the same
    value to ``launch_arguments``, so what is launched is what was judged.
    """
    LIVE_HUD_OUTPUT.require(fixed_hud_output_permitted(client, layout), output_refusal(client, layout))


def launch_arguments(validation_only, layout):
    """What follows the binary on the bridge's command line: the mode, then the layout when one is named.

    A plain function of its two arguments: ``ObservedFrameClient.start``
    reads the client's layout once, has that value judged
    (``require_real_output``) and hands the same value here, so what is
    launched is what was judged. No method of the client lies between the
    two.
    """
    mode = VALIDATION_MODE if validation_only is True else FIXED_HUD_MODE
    return (mode,) if layout is None else (mode, LAYOUT_ARGUMENT, layout.layout_id)


class ObservedFrameClient(OverlayClient):
    """The Python end of the bridge's observed-frame path (opcode 6): the fixed HUD's only sender.

    It starts the bridge in validation (``--validate-stdin``, which takes
    opcode 6 under the fixed HUD's rules and displays nothing) or, with
    ``validation_only`` false, in ``--fixed-hud``. The second is real output
    and is refused before anything is launched (``check_start``, through the
    ``LIVE_HUD_OUTPUT`` gate) unless ``fixed_hud_output_permitted``, the single
    place that decides, finds a one-run scope record that verifies on
    ``scope`` and binds the layout this client is about to launch. ``scope`` is a ``runtime.scope.RunScope`` or None; a
    validation client never consults it. The mode is fixed at construction:
    ``validation_only`` cannot be assigned afterwards. ``max_output_seconds``
    is the client's own bound on real output: that long after ``start`` is
    called the writer drops what waits, writes the hide and the stop packet
    and accepts nothing more (``output_bound_reached``), whatever its caller
    is doing. The permission site refuses a real-output client without it.

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
                 listener=None, scope=None, max_output_seconds=None, layout=None):
        if type(validation_only) is not bool:
            raise ValueError("observed_frame_client_mode_invalid")
        # The layout is one of the contract's own objects or nothing: a copy, an edited copy or a name is refused.
        if layout is not None and not any(layout is known for known in FIXED_LAYOUTS):
            raise ValueError("observed_frame_layout_unknown")
        if (not isinstance(keepalive_seconds, (int, float)) or not math.isfinite(keepalive_seconds)
                or not 0 < keepalive_seconds <= KEEPALIVE_MAX_SECONDS):
            raise ValueError("observed_frame_keepalive_invalid")
        if max_output_seconds is not None and (type(max_output_seconds) not in (int, float)
                                               or not math.isfinite(max_output_seconds) or max_output_seconds <= 0):
            raise ValueError("observed_frame_output_bound_invalid")
        super().__init__(binary, validation_only=validation_only, env=env)
        self._layout = layout
        self.max_output_seconds = max_output_seconds
        self.output_bound_reached = False
        self._output_deadline = None  # ``time.monotonic()`` after which real output ends; set by ``start``.
        self.prerequisites = ()  # Callables the output site asks, each for a literal True, before it consumes.
        self.keepalive_seconds, self.listener = float(keepalive_seconds), listener
        self.scope = scope  # Read only by the two permission sites.
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

    @property
    def validation_only(self):
        return self._validation_only

    @validation_only.setter
    def validation_only(self, value):
        if hasattr(self, "_validation_only"):  # Set once, by the constructor: the mode the gates saw is the mode started.
            raise AttributeError("observed_frame_client_mode_is_fixed")
        self._validation_only = value

    @property
    def layout(self):
        """The fixed layout named at construction, or None (the strip's client of before layouts); never assigned."""
        return self._layout

    def bridge_mode(self):
        return VALIDATION_MODE if self.validation_only else FIXED_HUD_MODE

    def check_start(self):
        """Refuse real output: validation starts freely, ``--fixed-hud`` only if the one permission check says so.

        The check is asked every time and nothing is kept here. The scope
        remembers which client it granted real output to; when that client
        asks again (``start`` after the stage's ``check_start``) the scope
        answers after checking the deadline and the bridge's digest once
        more, and consumes nothing.
        """
        if self.validation_only is not True:
            require_real_output(self, self._layout)

    def withdraw(self):
        """A run that was refused or failed before the bridge started gives its scope back for good.

        The scope is revoked, so nothing can start under it later; its run id
        stays spent. Without a scope, or once the bridge has started, this
        does nothing.
        """
        if self.process is None and type(self.scope) is RunScope:
            self.scope.revoke()

    def start(self):
        """Start the bridge in this client's mode, after the gate; otherwise as ``OverlayClient.start``.

        In real output the permission is asked again here, right before the
        launch. What remains between that answer and the launch is the launch
        itself. The client's own bound on real output counts from this call.

        The layout is read once, here. That one value is judged by the
        permission site (``require_real_output``, which compares it with the
        layout the run's record binds) and the command line is built from it
        (``launch_arguments``); neither goes through a method or property a
        subclass could answer differently, and the judgement here does not
        go through ``check_start`` either, so the layout launched is the
        layout judged, whatever ``check_start`` or ``layout`` have been made
        to say.
        """
        layout, validation_only = self._layout, self.validation_only is True
        if not validation_only:  # The site itself, on this value: an overridden ``check_start`` changes nothing.
            require_real_output(self, layout)
        if self.validation_only is not True and self._started_real_output:
            raise ValueError("observed_frame_client_already_started")  # One permitted start, one bridge.
        self._started_real_output = self.validation_only is not True
        if self.validation_only is not True and self.max_output_seconds is not None:
            self._output_deadline = time.monotonic() + self.max_output_seconds
        if not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            raise RuntimeError("Overlay bridge missing; run bash scripts/build_overlay_bridge.sh")
        self.process = subprocess.Popen([str(self.binary), *launch_arguments(validation_only, layout)],
                                        stdin=subprocess.PIPE,
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
            # A bridge names its layout exactly when it was launched with one: the name asked for, or none.
            if message.get("layout_id") != (None if self._layout is None else self._layout.layout_id):
                raise RuntimeError("overlay_bridge_layout_mismatch")
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
        back unchanged to the listener. A client that names a layout takes
        that layout's frame alone: exactly its width and height, four bytes
        a pixel (for the box panel, its renderer's RGBA bytes); anything
        else is ``observed_frame_not_layout_size``, before it is queued.
        """
        if self._layout is not None and (
                type(width) is not int or type(height) is not int
                or (width, height) != (self._layout.width_px, self._layout.height_px)
                or len(rgba) != width * height * RGBA_BYTES):
            raise ValueError("observed_frame_not_layout_size")
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
                        wake = last_packet + self.keepalive_seconds
                        if self._output_deadline is not None:
                            wake = min(wake, self._output_deadline)
                        self.condition.wait(timeout=max(0.0, wake - time.monotonic()))
                    bound = (self._output_deadline is not None and time.monotonic() >= self._output_deadline
                             and not self.stopping.is_set())
                    if bound:  # The client's own bound on real output: as ``end``, then the stop packet below.
                        self.output_bound_reached = self.final = self._want_hidden = True
                    frame, self._pending_observed = self._pending_observed, None
                    hide = self._want_hidden
                if bound and frame is not None:
                    self._notify("frame_not_sent", frame_id=frame[1], reason="output_bound_reached", token=frame[4])
                    frame = None
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
                if bound:
                    self.stopping.set()
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
                if self._layout is not None:
                    # The bridge's own record of the layout it was pinned to must be the one this client named.
                    layout_id, layout_digest = self._layout.layout_id, config_digest(self._layout)
                    block = native.get("validation_layout" if self.validation_only else "fixed_hud")
                    block = block if type(block) is dict else {}
                    mode_ok = mode_ok and (
                        (block.get("config_id"), block.get("config_digest"), block.get("width"), block.get("height"))
                        == (layout_id, layout_digest, self._layout.width_px, self._layout.height_px))
                    report.update(layout_id=layout_id, layout_digest=layout_digest)
                report.update(
                    started=started, mode=self.bridge_mode(), frames_submitted=self.frames_submitted,
                    frames_replaced=self.frames_replaced, frames_withdrawn=self.frames_withdrawn,
                    frames_expired_before_send=self.frames_expired_before_send, frames_retransmitted=0,
                    take_downs=self.take_downs, shows=self.shows, keepalives=self.keepalives,
                    visibility_requests=self.visibility_requests, settings_sent=0,
                    frames_presented=self.presented_count, frames_refused=self.refused_count,
                    refused_reasons=dict(self.refused_reasons),
                    expiry_events=self.expiry_event_count, expiry_events_dropped=self.expiry_events_dropped,
                    max_output_seconds=self.max_output_seconds, output_bound_reached=self.output_bound_reached,
                    bridge_counts={name: native.get(name) for name in BRIDGE_COUNTS},
                    cleanup_confirmed=(not started and self.error is None) or (bridge_closed_cleanly(report) and mode_ok))
                self._closed_report = report
            return self._closed_report
