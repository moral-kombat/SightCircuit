"""The one supervision primitive: an owned worker session, ended on a deadline, with one report block.

Seeded from the runtime guard's owned-session supervisor (``guard._supervise_owned``
with ``child_reaper`` and ``_reap_members``), this module is what every
launcher of a bounded child is meant to use: the guard adopts it first, the
agent runner, the live pipeline and mirror launcher and the paused capture
supervisor follow (architecture decision D4). It offers:

- **Owned-session spawning.** The worker starts in a new session with the
  descriptors the caller passes (``pass_fds``) and, on POSIX, an inherited
  *arm* descriptor (``SIGHTCIRCUIT_ARMED_FD``) it writes to once its signal
  handlers are installed (``report_armed``, called by ``guard.stop_on_signals``),
  plus ``SIGHTCIRCUIT_COOPERATIVE_GRACE``, the seconds it has to exit after
  SIGTERM, so that its own cleanup joins can share the supervisor's deadline
  (``cooperative_budget``).
- **Hold-and-arm protocol around process start.** A ``Signals`` handler
  starts held: in ``raise`` mode a signal only raises ``Cancelled`` once the
  caller has armed it (``arm``), and ``hold``/``resume`` bracket a region,
  such as a ``Popen``, where there is nothing to terminate yet. The primitive
  itself keeps its handler held for the whole supervision, polls the first
  signal (every 20 ms) like ``record`` mode, and in ``raise`` mode raises
  ``Cancelled`` carrying the finished report after the cleanup, so no window
  between process start, cleanup and report can lose a signal or skip the
  report block.
- **Group-wide termination with reaping.** SIGTERM to the leader, then
  ``killpg`` TERM and KILL, reaping every exited member the supervisor owns,
  as the guard did; a group a killed guardian reported is contained the same way.
- **Deadline-based cleanup verdicts.** A cancellation first waits up to
  ``Grace.arm`` for a worker that has not armed yet, so a signal that arrives
  while the worker is still starting is answered by its handler and not by
  the default disposition. The leader's exit and the completion of its report
  are then awaited together until one cooperative deadline; the fixed
  sub-second joins of the earlier supervisors are gone (``Grace.report`` is a
  reserve for an orphaned pipe after the tree is gone, never the verdict).
- **Both signal modes with the inherited-ignore policy as a parameter.**
  ``RECORD_AND_POLL`` (the guard's: record the first signal, override an
  inherited ignore so a launcher can always be cancelled) and
  ``RAISE_ON_SIGNAL`` (the agent runner's: raise, and leave a signal the
  process was started with ignored, as ``nohup`` ignores SIGHUP, ignored).
- **Progress streaming.** A worker may write newline-delimited JSON events
  on its report stream: ``{"event": "progress", "report": {...}}`` reaches
  the caller's ``progress`` callback on the supervising thread, and
  ``{"event": "result", "report": {...}}`` or a bare JSON report is the
  final report. The guard's guardian forwards events unchanged.
- **The worker's stop relay** (``stop_on``, behind ``guard.stop_on_signals``).
  A stop signal sets the worker's ``threading.Event`` from a relay thread that
  the signal's C-level delivery wakes through the process's wakeup descriptor
  (``signal.set_wakeup_fd``); the Python-level handler itself takes no lock.
  A handler that called ``Event.set()`` on the main thread deadlocked when the
  signal landed inside ``Event.wait()``'s own critical section, and was not run
  at all while the main thread blocked, uninterrupted, in the wait's lock
  acquire (the lane B flake; see the checks reference). Neither can happen to
  a relay thread.
- **One report block.** ``status``, ``exit_code``, ``cleanup_ok``,
  ``reason``, ``worker_pid``, ``worker_armed``, ``worker_reaped``,
  ``worker_exit_code``, ``worker_tree_reaped``, ``report_reader_stopped``,
  ``worker_forced_termination``, ``worker_termination_requested`` and
  ``supervisor_elapsed_seconds``, with the same meanings as before.

The Python side owns escalation and the cleanup verdict; a worker may end
itself (watchdog, parent death) but supervises nothing here. Grace numbers
are tunables (``Grace``), not guarantees about kernel scheduling or blocked
kernel I/O; an unkillable process is reported unconfirmed.
"""
import collections
import contextlib
from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import threading
import time
import weakref

MAX_REPORT_BYTES = 256 * 1024
ARMED_FD_VARIABLE = "SIGHTCIRCUIT_ARMED_FD"
COOPERATIVE_GRACE_VARIABLE = "SIGHTCIRCUIT_COOPERATIVE_GRACE"
SUPERVISOR_PID_VARIABLE = "SIGHTCIRCUIT_SUPERVISOR_PID"
PR_SET_PDEATHSIG = 1
ROOT = Path(__file__).resolve().parents[2]
WORKER_PACKAGES = ("sightcircuit", "scripts", "tests")  # Where a worker function may live (``load_function``).
RECORD, RAISE = "record", "raise"
OVERRIDE, HONOUR = "override", "honour"


class Cancelled(BaseException):
    """The first interrupting signal, raised in ``raise`` mode; after cleanup it carries the report."""

    def __init__(self, number, report=None):
        super().__init__(number)
        self.number = number
        self.report = report


@dataclass(frozen=True)
class SignalPolicy:
    """Which signals a supervisor takes over, what the first one does, and what an inherited ignore means.

    ``mode`` is ``record`` (the first signal is recorded and polled) or ``raise``
    (it raises ``Cancelled`` while the handler is armed). ``inherited_ignore``
    is ``override`` (a handler is installed even for a signal the process was
    started with ignored) or ``honour`` (such a signal stays ignored).
    """
    mode: str = RECORD
    inherited_ignore: str = OVERRIDE
    numbers: tuple = tuple(getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, name))

    def __post_init__(self):
        if self.mode not in (RECORD, RAISE) or self.inherited_ignore not in (OVERRIDE, HONOUR):
            raise ValueError("unsupported_signal_policy")


RECORD_AND_POLL = SignalPolicy(RECORD, OVERRIDE)
RAISE_ON_SIGNAL = SignalPolicy(RAISE, HONOUR)


class Signals:
    """The policy's handlers, installed on the main thread for the block; ``number`` is the first signal seen.

    The handler starts held: a signal is only noted until ``arm()``. In
    ``raise`` mode an armed handler raises ``Cancelled`` on the first signal,
    and ``hold()``/``resume(armed)`` bracket a region where a signal may only
    be noted (a process start, a cleanup); ``resume`` and ``arm`` raise a
    signal noted meanwhile. A signal is delivered (raised) at most once;
    later signals are only counted.
    """

    def __init__(self, policy=RECORD_AND_POLL, exception=Cancelled):
        self.policy = policy
        self.exception = exception  # Raised in ``raise`` mode, with the signal number; a caller's own subclass fits.
        self.number = None
        self.recorded = []  # Every signal seen, first first; ``number`` is recorded[0].
        self.armed = False
        self.delivered = False  # Raised once already; never again.
        self.previous = {}
        self.handled = ()  # The signals whose handlers this block installed.

    @property
    def received(self):
        return self.number is not None

    @property
    def count(self):
        return len(self.recorded)

    def handle(self, number, _frame):
        self.recorded.append(number)
        if self.number is None:
            self.number = number
            self.deliver()

    def deliver(self):
        """In ``raise`` mode, raise the noted signal if the handler is armed and it was not raised before."""
        if self.policy.mode == RAISE and self.armed and self.number is not None and not self.delivered:
            self.delivered = True
            raise self.exception(self.number)

    def hold(self):
        """From now on only note a signal; returns whether the handler was armed, for ``resume``."""
        armed, self.armed = self.armed, False
        return armed

    def resume(self, armed):
        """Re-arm (when ``hold`` found it armed); in ``raise`` mode a signal noted meanwhile is raised."""
        self.armed = armed
        self.deliver()

    def arm(self):
        """Arm the handler; in ``raise`` mode a signal noted while it was held is raised now."""
        self.resume(True)

    @contextlib.contextmanager
    def holding(self):
        """Hold for the block (a process start, an ending); on exit resume, raising a signal noted meanwhile."""
        armed = self.hold()
        try:
            yield self
        finally:
            self.resume(armed)

    def __enter__(self):
        if threading.current_thread() is not threading.main_thread():
            raise ValueError("worker_supervisor_requires_main_thread")
        installed = []
        for number in self.policy.numbers:
            if self.policy.inherited_ignore == HONOUR and signal.getsignal(number) == signal.SIG_IGN:
                continue
            self.previous[number] = signal.signal(number, self.handle)
            installed.append(number)
        self.handled = tuple(installed)
        return self

    def __exit__(self, *_):
        for number, handler in self.previous.items():
            signal.signal(number, handler)
        self.previous = {}


@dataclass(frozen=True)
class Grace:
    """Cleanup budgets in seconds; every one is a tunable, none a guarantee.

    ``arm``: how long a cancellation waits for a worker that has not armed its
    handlers yet. ``cooperative``: after SIGTERM, for the leader to exit with
    its report complete. ``terminate`` and ``kill``: after ``killpg`` TERM and
    KILL, for the group to be gone. ``report``: a reserve for the report reader
    after the tree is gone (an orphaned pipe end), not part of the verdict.
    """
    arm: float = 1.0
    cooperative: float = 5.0
    terminate: float = 1.0
    kill: float = 1.0
    report: float = 1.0

    def total(self):
        """The longest a cleanup can take with these budgets."""
        return self.arm + self.cooperative + self.terminate + self.kill + self.report

    def as_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, values):
        return cls(**{key: float(values[key]) for key in asdict(cls()) if key in values})

    def for_supervisor_of(self):
        """The grace a supervisor of a process that itself cleans up with *these* budgets needs."""
        return Grace(arm=self.arm, cooperative=self.total() + 1, terminate=self.terminate, kill=self.kill,
                     report=self.report)


GRACE = Grace()


def report_armed():
    """Tell the supervisor this worker's signal handlers are installed; True if a supervisor was listening.

    Called by ``guard.stop_on_signals`` after it installs the worker's
    handlers. The descriptor is closed and its variable removed, so a
    descendant started later can neither inherit nor misuse it.
    """
    value = os.environ.pop(ARMED_FD_VARIABLE, None)
    if value is None or not value.isdigit():
        return False
    fd = int(value)
    try:
        os.write(fd, b"armed\n")
    except OSError:
        return False
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)
    return True


STOP_SIGNALS = tuple(getattr(signal, name) for name in ("SIGINT", "SIGTERM") if hasattr(signal, name))


class StopWakeup:
    """The process's stop relay: a stop signal's delivery wakes a thread that sets every registered event.

    The Python-level handler a worker installs runs on the main thread, between
    two bytecodes, and only when the main thread gets to run Python code at all.
    Setting the worker's stop event from it is therefore unsafe in two ways
    that both ended workers by SIGKILL in CI: the handler ran while the main
    thread was inside ``Event.wait()`` holding the event's (non-reentrant)
    condition lock, so ``Event.set()`` deadlocked on the thread that held it;
    and a signal whose C-level handler tripped just before the main thread
    blocked in the wait's lock acquire was not handled until that acquire
    returned, since only a signal arriving *during* the acquire interrupts it.

    Here the handler only writes a byte to a pipe (``handle``; no lock), and
    the pipe's write end is also the process's wakeup descriptor
    (``signal.set_wakeup_fd``), so the C-level delivery of any Python-handled
    signal writes its number to it at once, from whichever thread received it,
    whatever the main thread is doing. The relay thread reads the pipe and
    sets every registered event on a stop signal's byte; it takes the event's
    lock on its own thread, where a main thread holding it is contention, not
    a deadlock. Bytes of other signals are ignored. One relay serves the
    process; a forked child forgets it (``os.register_at_fork``), so it cannot
    write into a pipe its parent's thread reads.
    """

    def __init__(self):
        self.read_fd, self.write_fd = os.pipe()
        os.set_blocking(self.write_fd, False)
        self.events = weakref.WeakSet()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self.relay, name="sightcircuit-stop-relay", daemon=True)
        self.thread.start()
        self.previous_wakeup_fd = signal.set_wakeup_fd(self.write_fd, warn_on_full_buffer=False)
        os.register_at_fork(after_in_child=self.forget)

    def register(self, stop):
        with self.lock:
            self.events.add(stop)

    def handle(self, number, _frame):
        """The signal handler: one non-blocking write, nothing that takes a lock.

        The C-level delivery already wrote the signal's number to the wakeup
        descriptor; this byte covers a wakeup descriptor another library
        replaced after the relay was installed. A full pipe means a byte is
        already waiting, so the relay wakes anyway.
        """
        try:
            os.write(self.write_fd, bytes([number]))
        except OSError:
            pass

    def relay(self):
        while True:
            try:
                data = os.read(self.read_fd, 64)
            except InterruptedError:
                continue
            except OSError:
                return
            if not data:
                return
            if any(byte in STOP_SIGNALS for byte in data):
                with self.lock:
                    events = list(self.events)
                for event in events:
                    event.set()

    def forget(self):
        """In a forked child: no relay thread exists, and the parent's pipe is not ours to write."""
        global _stop_wakeup
        _stop_wakeup = None
        with contextlib.suppress(ValueError, OSError):
            signal.set_wakeup_fd(-1)
        for fd in (self.read_fd, self.write_fd):
            with contextlib.suppress(OSError):
                os.close(fd)


_stop_wakeup = None


def stop_on(stop, numbers=STOP_SIGNALS):
    """Make the signals ``numbers`` (SIGINT and SIGTERM) set the event ``stop``; returns it.

    On POSIX the setting is done by the process's ``StopWakeup`` relay, woken
    by the signal's C-level delivery, and the installed handler takes no lock.
    Elsewhere (no wakeup pipe) the handler sets the event itself, as before.
    Call on the main thread, once per event; later calls add events.
    """
    global _stop_wakeup
    if os.name != "posix":
        for number in numbers:
            signal.signal(number, lambda *_: stop.set())
        return stop
    if _stop_wakeup is None:
        _stop_wakeup = StopWakeup()
    _stop_wakeup.register(stop)
    for number in numbers:
        signal.signal(number, _stop_wakeup.handle)
    return stop


def enter_worker(number=signal.SIGTERM):
    """A supervised worker's parent-death guard; False if its supervisor is already gone.

    On Linux the worker asks for ``number`` (SIGTERM, which ``guard.stop_on_signals``
    turns into a cooperative stop) when its parent thread dies, so a worker is
    not stranded by a supervisor killed during a driver call. The check against
    ``SIGHTCIRCUIT_SUPERVISOR_PID`` (set by ``supervise_owned``) closes the gap in
    which the parent died before the request was made. Elsewhere, or outside a
    supervisor, it only reports True.
    """
    if sys.platform.startswith("linux"):
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(PR_SET_PDEATHSIG, int(number), 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "cannot_enable_parent_death_signal")
    expected = os.environ.get(SUPERVISOR_PID_VARIABLE)
    return not (expected and expected.isdigit()) or os.getppid() == int(expected)


def emit_event(event, report, stream=None):
    """Write one ``{"event": ..., "report": ...}`` line to the worker's report stream (stdout) and flush it."""
    stream = sys.stdout if stream is None else stream
    stream.write(json.dumps({"event": event, "report": report}) + "\n")
    stream.flush()


def function_reference(function):
    """``{"module", "function"}`` naming a module-level function of this checkout, for a worker to import.

    The module is named by its file's path under the checkout root (so a
    script run as ``__main__`` and a test module loaded from ``tests/`` both
    resolve), as a spawned worker was once pickled by reference.
    """
    module = sys.modules.get(getattr(function, "__module__", None))
    path = Path(getattr(module, "__file__", None) or "").resolve()
    name = getattr(function, "__name__", "")
    try:
        parts = path.relative_to(ROOT).with_suffix("").parts
    except ValueError:
        parts = ()
    if (path.suffix != ".py" or len(parts) < 2 or parts[0] not in WORKER_PACKAGES or
            not all(part.isidentifier() for part in parts) or not name.isidentifier() or
            getattr(module, name, None) is not function):
        raise ValueError("worker_must_be_module_level_in_checkout")
    return {"module": ".".join(parts), "function": name}


def load_function(reference):
    """Import a ``function_reference`` from this checkout only."""
    import importlib
    import importlib.util
    module_name, function_name = reference.get("module", ""), reference.get("function", "")
    if (not isinstance(module_name, str) or not module_name or
            not all(part.isidentifier() for part in module_name.split(".")) or
            not isinstance(function_name, str) or not function_name.isidentifier()):
        raise ValueError("unsupported_worker_reference")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, ValueError):
        spec = None
    origin = Path(spec.origin) if spec is not None and spec.origin else None
    # A real file of this checkout's own packages (never a frozen or built-in module, whose pseudo-origin would
    # resolve against the working directory, and never a package installed under the checkout).
    if (origin is None or not origin.is_absolute() or not origin.is_file() or
            not any(origin.resolve().is_relative_to(ROOT / package) for package in WORKER_PACKAGES)):
        raise ValueError("unsupported_worker_reference")
    return getattr(importlib.import_module(module_name), function_name)


def cooperative_budget(default, reserve=1.0):
    """Seconds a supervised worker may spend on one cleanup join: the supervisor's cooperative grace less ``reserve``.

    Outside a supervisor (no ``SIGHTCIRCUIT_COOPERATIVE_GRACE``), or when the
    grace leaves less than ``default``, it is ``default``; the result never
    grows a budget that was already longer than the grace allows.
    """
    value = os.environ.get(COOPERATIVE_GRACE_VARIABLE)
    try:
        grace = float(value) if value is not None else None
    except ValueError:
        grace = None
    if grace is None:
        return default
    return max(default, grace - reserve) if grace - reserve > default else default


@contextlib.contextmanager
def child_reaper():
    """On Linux, make this process the subreaper of its descendants for the block (restored afterwards)."""
    if not sys.platform.startswith("linux"):
        yield
        return
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(previous), 0, 0, 0) or libc.prctl(36, 1, 0, 0, 0):
        raise OSError(ctypes.get_errno(), "cannot_establish_worker_child_reaper")
    try:
        yield
    finally:
        if libc.prctl(36, previous.value, 0, 0, 0):
            raise OSError(ctypes.get_errno(), "cannot_restore_worker_child_reaper")


def reap_members(pgid):
    """Reap exited members of process group ``pgid`` that are this process's children."""
    while True:
        try:
            pid, _ = os.waitpid(-pgid, os.WNOHANG)
        except ChildProcessError:
            return
        if not pid:
            return


def group_exists(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


def reported_group(fd):
    """The worker group a guardian wrote to ``fd`` and never confirmed gone, else None."""
    os.set_blocking(fd, False)
    try:
        lines = os.read(fd, 32).decode("ascii", "replace").split()
    except BlockingIOError:
        return None
    if len(lines) != 1 or not lines[0].isdigit() or int(lines[0]) <= 1:
        return None  # Nothing reported, or the guardian confirmed its tree "reaped".
    return int(lines[0])


def contain_orphaned_group(pgid, grace=GRACE):
    """SIGTERM, then SIGKILL, a killed guardian's worker group; True once it is gone.

    The guardian never confirmed this group gone. While any member lives, or
    the worker waits unreaped as this subreaper's child, ``pgid`` names it.
    """
    for number, seconds in ((signal.SIGTERM, grace.terminate), (signal.SIGKILL, grace.kill)):
        try:
            os.killpg(pgid, number)
        except ProcessLookupError:
            return True
        deadline = time.monotonic() + seconds
        while True:
            reap_members(pgid)
            if not group_exists(pgid):
                return True
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
    return False


class OwnedGroup:
    """The process group of a child started in its own session (``start_new_session``), ended and reaped by this process.

    ``alive()`` reaps the leader and, under ``child_reaper``, every exited
    member reparented to this process, then asks the kernel whether the group
    still has a member. ``terminate(grace)`` is the one escalation: ``killpg``
    SIGTERM, wait ``grace.terminate``, ``killpg`` SIGKILL, wait ``grace.kill``;
    it returns whether the group is gone. A member that leaves the group
    (``setsid``) is out of reach, as documented for every supervisor here.
    """

    def __init__(self, process):
        self.process = process
        self.posix = os.name == "posix"

    @property
    def pgid(self):
        return self.process.pid

    def alive(self):
        """True while the leader or any member of its group exists."""
        if not self.posix:
            return self.process.poll() is None
        # poll reaps our direct child first; only then reap adopted group members.
        if self.process.poll() is not None and sys.platform.startswith("linux"):
            reap_members(self.pgid)
        return group_exists(self.pgid)

    def signal(self, number):
        try:
            if self.posix:
                os.killpg(self.pgid, number)
            elif self.process.poll() is None:
                self.process.kill()
        except ProcessLookupError:
            pass

    def wait_gone(self, seconds):
        """Wait until the group is gone, at most ``seconds``; True once it is."""
        deadline = time.monotonic() + seconds
        while self.alive():
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)
        return True

    def terminate(self, grace=GRACE):
        """SIGTERM the group, then SIGKILL what is still there after ``grace.terminate``; True once the group is gone."""
        self.signal(signal.SIGTERM)
        if self.wait_gone(grace.terminate):
            return True
        self.signal(getattr(signal, "SIGKILL", signal.SIGTERM))
        return self.wait_gone(grace.kill)


class ReportStream:
    """Reads a worker's report stream off-thread: events are queued, everything else is the report text.

    A line that parses as a JSON object with an ``event`` key is an event
    (``progress`` with a ``report``, ``result`` with the final ``report``);
    any other line is report text, so a one-line or an indented final report
    both parse. No pending line or report may exceed ``MAX_REPORT_BYTES``.
    """

    def __init__(self, stream):
        self.stream = stream
        self.text = bytearray()
        self.events = collections.deque()
        self.result = None
        self.errors = []
        self.done = threading.Event()
        self.thread = threading.Thread(target=self.read, daemon=True)

    def start(self):
        self.thread.start()

    def read(self):
        pending = bytearray()
        try:
            while True:
                data = self.stream.read(8192)
                if not data:
                    break
                pending.extend(data)
                if len(pending) > MAX_REPORT_BYTES:
                    raise ValueError("worker_report_byte_limit")
                while True:
                    line, newline, rest = pending.partition(b"\n")
                    if not newline:
                        break
                    self.line(line)
                    pending = bytearray(rest)
            if pending:
                self.line(pending)
        except (OSError, ValueError) as error:
            self.errors.append(str(error))
        finally:
            self.stream.close()
            self.done.set()

    def line(self, line):
        try:
            message = json.loads(line)
        except ValueError:
            message = None
        if isinstance(message, dict) and "event" in message:
            self.events.append(message)
            return
        self.text.extend(line)
        self.text.extend(b"\n")
        if len(self.text) > MAX_REPORT_BYTES:
            raise ValueError("worker_report_byte_limit")

    def dispatch(self, progress):
        """Hand queued events to ``progress`` on the caller's thread; a ``result`` event becomes the final report."""
        while self.events:
            message = self.events.popleft()
            if message.get("event") == "result" and isinstance(message.get("report"), dict):
                self.result = message["report"]
            elif message.get("event") == "progress" and progress is not None and isinstance(message.get("report"), dict):
                progress(message["report"])

    def candidate(self):
        """The final report: the ``result`` event's, else the parsed report text."""
        if self.result is not None:
            return self.result
        return json.loads(self.text)


def supervise_owned(command, base_report, timeout, validate, *, descendants=False, parent_fd=None, pass_fds=(),
                    grace=GRACE, group_fd=None, nested_group_fd=None, progress=None, signals=RECORD_AND_POLL,
                    env=None):
    """Run ``command`` in an owned session for at most ``timeout`` seconds and accept its report only if ``validate`` does.

    Returns the report (``record`` mode); in ``raise`` mode a cancellation
    completes the cleanup and the report, then raises ``Cancelled`` carrying
    it. ``parent_fd`` is a lifeline whose readability means the supervisor's
    parent is gone; ``group_fd`` receives the worker's group and then
    "reaped", for a supervisor that contains an unconfirmed group read from
    ``nested_group_fd`` if this process is killed. Every escalation and
    surviving process is reflected in ``cleanup_ok``, independently of the
    worker's own claims. Descendants must stay in the owned group; worker
    code cannot detach helpers.
    """
    if descendants and not sys.platform.startswith("linux"):
        raise ValueError("native_helper_supervision_requires_linux")
    posix = os.name == "posix"
    report = dict(base_report, status="error", exit_code=1, cleanup_ok=False)
    process = stream = group = None
    owned = {}
    forced, termination_requested, tree_gone, armed = False, False, False, False
    arm_read = arm_write = None
    failure = None
    started = time.monotonic()

    def parent_lost():
        return parent_fd is not None and bool(select.select([parent_fd], [], [], 0)[0])

    def check_armed(seconds=0):
        """Read the arm descriptor for up to ``seconds``; True once the worker has armed."""
        nonlocal armed, arm_read
        if armed or arm_read is None:
            return armed
        if select.select([arm_read], [], [], seconds)[0]:
            data = os.read(arm_read, 16)
            if data:
                armed = True
            else:  # Closed without a word: the worker is gone or never arms.
                os.close(arm_read)
                arm_read = None
        return armed

    def wait_armed(seconds):
        deadline = time.monotonic() + seconds
        while not check_armed(min(0.05, max(0, deadline - time.monotonic()))):
            if process.poll() is not None or arm_read is None or time.monotonic() >= deadline:
                break

    def end_worker(cancelling):
        nonlocal forced, termination_requested, tree_gone
        if process.poll() is None:
            if cancelling:
                wait_armed(grace.arm)
            termination_requested = True
            if posix:
                try:
                    process.send_signal(signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                forced = True  # Windows terminate cannot run Python finally blocks.
                process.terminate()
            # One deadline for the leader's exit and the end of its report.
            deadline = time.monotonic() + grace.cooperative
            while time.monotonic() < deadline:
                if process.poll() is not None and (stream.done.is_set() or not group.alive()):
                    break
                stream.done.wait(0.02)
        tree_gone = not group.alive()
        if not tree_gone:
            forced = True
            tree_gone = group.terminate(grace)

    def contain_orphan():
        nonlocal forced, tree_gone
        orphan = (reported_group(nested_group_fd) if nested_group_fd is not None and
                  process.returncode is not None and process.returncode < 0 else None)
        if orphan is None:
            return
        # A killed guardian ran no cleanup; its worker group is ours to end.
        reap_members(orphan)
        signalled = group_exists(orphan)
        orphan_gone = not signalled or contain_orphaned_group(orphan, grace)
        forced = forced or signalled
        tree_gone = tree_gone and orphan_gone
        owned.update(orphaned_worker_group=orphan, orphaned_worker_tree_signalled=signalled,
                     orphaned_worker_tree_reaped=orphan_gone)

    # The handler stays held throughout: the loop polls the first signal, and in raise mode ``Cancelled``
    # is raised below with the finished report, so no window around Popen, the cleanup or the report
    # block can lose a signal or leave a process behind.
    with Signals(signals) as handlers, child_reaper():
        report_armed()  # A nested supervisor (the guard's guardian) is its parent's worker: its handlers are in.
        try:
            if parent_lost():
                raise ValueError("supervisor_lifeline_closed_before_start")
            extra = {}
            if posix:
                arm_read, arm_write = os.pipe()
                environment = dict(os.environ if env is None else env)
                environment[ARMED_FD_VARIABLE] = str(arm_write)
                environment[COOPERATIVE_GRACE_VARIABLE] = repr(grace.cooperative)
                environment[SUPERVISOR_PID_VARIABLE] = str(os.getpid())
                extra = {"pass_fds": (*pass_fds, arm_write), "env": environment, "start_new_session": True}
            elif env is not None:
                extra = {"env": env}
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, bufsize=0,
                                       **extra)
            group = OwnedGroup(process)
            if arm_write is not None:
                os.close(arm_write)
                arm_write = None
            report["worker_pid"] = process.pid
            if group_fd is not None:
                # A dead supervisor is detected below through the lifeline.
                with contextlib.suppress(BrokenPipeError):
                    os.write(group_fd, b"%d\n" % process.pid)
            stream = ReportStream(process.stdout)
            stream.start()
            deadline = started + timeout
            while True:
                stream.dispatch(progress)
                check_armed()
                if parent_lost():
                    failure = "supervisor_lost"
                    break
                if handlers.received:
                    failure = "cancelled"
                    break
                if time.monotonic() >= deadline:
                    failure = "timed_out"
                    break
                if stream.errors:
                    raise ValueError(stream.errors[0])
                if process.poll() is not None:
                    # A descendant may keep stdout open after the worker exits.
                    if stream.done.wait(0.05):
                        break
                    failure = "worker_exited_with_open_report_pipe"
                    break
                stream.done.wait(0.02) if not stream.done.is_set() else time.sleep(0.02)
        except (OSError, ValueError) as error:
            failure = "error"
            report["reason"] = str(error)[:1024]
        finally:
            if arm_write is not None:
                os.close(arm_write)
            if process is not None:
                end_worker(failure in ("cancelled", "supervisor_lost") or handlers.received)
                if group_fd is not None and tree_gone:
                    with contextlib.suppress(BrokenPipeError):
                        os.write(group_fd, b"reaped\n")
                contain_orphan()
                stream.thread.join(timeout=grace.report)
                check_armed()
                forced = forced or (process.returncode is not None and process.returncode < 0)
                owned.update(worker_pid=process.pid, worker_armed=armed, worker_reaped=process.poll() is not None,
                             worker_exit_code=process.returncode, worker_tree_reaped=tree_gone,
                             report_reader_stopped=not stream.thread.is_alive())
            else:
                owned.update(worker_armed=False, worker_reaped=False, worker_tree_reaped=False,
                             report_reader_stopped=True)
            if arm_read is not None:
                os.close(arm_read)
                arm_read = None
        if handlers.received:
            failure = "cancelled"
        try:
            if stream is not None:
                stream.dispatch(progress)
                if stream.errors:
                    raise ValueError(stream.errors[0])
            if process is None or not stream.done.is_set():
                raise ValueError("worker_report_incomplete")
            candidate = stream.candidate()
            validate(candidate, process.returncode)
            report.update(candidate)
        except (ValueError, TypeError) as error:
            report.setdefault("reason", str(error)[:1024])
            failure = failure or "invalid_worker_report"
        report.update(owned)
        report.update(worker_forced_termination=forced, worker_termination_requested=termination_requested,
                      supervisor_elapsed_seconds=round(time.monotonic() - started, 3))
        if failure:
            report.update(status=failure, exit_code=128 + handlers.number if handlers.received else 1)
        # A cooperative cancellation may confirm cleanup, but timeout, invalid
        # reports, nonzero contradictions or forced kills never establish it.
        report["cleanup_ok"] = (report.get("cleanup_ok") is True and tree_gone and stream is not None and
                                stream.done.is_set() and owned.get("report_reader_stopped") is True and
                                owned.get("worker_reaped") is True and not forced and failure in (None, "cancelled"))
        if not report["cleanup_ok"] and report["exit_code"] == 0:
            report.update(status="cleanup_failed", exit_code=1)
        if handlers.received and signals.mode == RAISE:
            raise Cancelled(handlers.number, report)  # The one place a raise-mode signal surfaces.
    return report
