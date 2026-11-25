"""The informed start of the camera commands: the notices on the operator's terminal and the typed answer.

``camera_guide`` shows the same export notice in its window, without the
closing instruction to type (``export_notice_body``), above a Start button;
its click is recorded as ``operator_window``.

Shared by ``camera_recognize`` and ``camera_hud`` so that neither command
imports the other. Owner decision 2026-11-09 (and decision 8 of 2026-11-16:
one typed start per launch): the command prints the notices on its terminal,
requires an interactive stdin and the operator's exact typed ``yes``, and
only then starts the run. No flag, variable or piped text stands in for the
answer.

The typed answer starts a run and does nothing else. It is not an approval,
it creates no scope record and it opens no gate: a display run still needs a
one-run scope record that verifies (``runtime/scope.py``), and is refused
without one whatever was typed.
"""
import sys

from . import contract

CONFIRMATION_ANSWER = "yes"
OPERATOR_TERMINAL = "operator_terminal"  # A report's ``confirmation`` once the operator typed the answer here.
# A report's ``confirmation`` once the wearer clicked Start in ``camera_guide``'s window, under the same notice
# (owner decision 2026-11-19, "Yes, tap to start"). The typed answer stays for the terminal commands.
OPERATOR_WINDOW = "operator_window"
TYPED_CLOSING = f" Type {CONFIRMATION_ANSWER} to continue."  # How ``contract.EXPORT_NOTICE`` ends.
# What a display run adds to the helper's export notice (owner decision 14, 2026-11-16: camera-derived labels
# and arrows may be shown to the wearer during attended, bounded runs).
DISPLAY_NOTICE = (
    "camera_hud: shows the names of detected objects, with approximate arrows if asked, on a small tile at the "
    "edge of the view in the headset, for the bounds shown below. A name is taken down about one second after "
    "the camera frame it came from unless a newer frame shows it again. The tile shows no camera image. The command saves no image; its report holds "
    "counts, labels, timings and format metadata, printed on this terminal and written to a file only if you named "
    "--report. Stop with Ctrl-C at any passthrough change, any green input, or when the wearer asks. Typing yes "
    "starts this one run; it is not an approval and creates no scope record.")
# The same for a run on the box panel (work-order item B5): what the wearer will see is a box and a name over the
# scene, not a tile at the edge. The owner accepted this wording as it stands on 2026-11-25 (owner decision C of
# item B5, relayed by the coordinator: "Yes, accept it"); ``tests/test_camera_hud_command.py`` pins it sentence
# for sentence, so a change to it is a change the owner is asked about.
BOX_DISPLAY_NOTICE = (
    "camera_hud: draws a box with a name around each detected object, on a transparent panel in the middle of the "
    "view in the headset, for the bounds shown below. A box sits on its object only while the head is still; it "
    "trails when the head turns. A box is taken down about one second after the camera frame it came from unless "
    "a newer frame shows it again. The panel shows no camera image. The command saves no image; its report holds "
    "counts, labels, timings and format metadata, printed on this terminal and written to a file only if you named "
    "--report. Stop with Ctrl-C at any passthrough change, any green input, or when the wearer asks. Typing yes "
    "starts this one run; it is not an approval and creates no scope record.")
SYNTHETIC_NOTICE = (
    "camera_hud --synthetic-rows: shows a counter row on a small tile at the edge of the view in the headset, "
    "for the bounds shown below, with scheduled pauses in which the tile must go blank within about a second. "
    "No camera is opened and nothing is detected. Nothing is saved but the report of counts and timings, printed "
    "on this terminal and written to a file only if you named --report. Stop with Ctrl-C at any passthrough "
    "change, any green input, or when the wearer asks. Typing yes starts this one run; it is not an approval "
    "and creates no scope record.")


def export_notice_body():
    """``contract.EXPORT_NOTICE`` without its closing instruction to type the answer, for a window with a Start button.

    Derived from the constant, never a second copy: a notice that no longer
    ends with ``TYPED_CLOSING`` is a ``ValueError``, so the window cannot show
    a text that has drifted from the contract's.
    """
    if not contract.EXPORT_NOTICE.endswith(TYPED_CLOSING):
        raise ValueError("the export notice no longer ends with its typed-answer instruction")
    return contract.EXPORT_NOTICE[:-len(TYPED_CLOSING)]


def confirm(notices, bounds, *, stdin=None, output=None):
    """Print ``notices`` and the ``bounds`` line, then read the typed answer; ``None`` when confirmed.

    Everything goes to ``output`` (stderr, never the report stream); the
    answer is read from ``stdin``, which must be an interactive terminal,
    and must be exactly ``yes``. Otherwise the named refusal:
    ``confirmation_requires_terminal`` (nothing is printed),
    ``confirmation_unavailable`` (end of input) or ``confirmation_declined``.
    """
    stdin = sys.stdin if stdin is None else stdin
    output = sys.stderr if output is None else output
    try:
        interactive = stdin.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if not interactive:
        return "confirmation_requires_terminal"
    for notice in notices:
        output.write(notice + "\n")
    output.write(f"Bounds: {bounds}\nType {CONFIRMATION_ANSWER} to continue: ")
    output.flush()
    answer = stdin.readline()
    if not answer:
        return "confirmation_unavailable"
    return None if answer.rstrip("\r\n \t") == CONFIRMATION_ANSWER else "confirmation_declined"


def confirm_on_terminal(limits, cameras, *, stdin=None, output=None, notices=()):
    """The informed start of a camera run: the helper's export notice (``contract.EXPORT_NOTICE``, verbatim),
    then ``notices`` (a display run's), the run's bounds and the typed answer, as ``confirm``."""
    bounds = (f"{limits['max_frames']} frames, {limits['max_seconds']} seconds, {limits['fps']} frames a "
              f"second, cameras {','.join(str(index) for index in cameras)}.")
    return confirm((contract.EXPORT_NOTICE, *notices), bounds, stdin=stdin, output=output)


def confirm_synthetic_rows(seconds, hz, *, stdin=None, output=None):
    """The informed start of the synthetic-rows run: its notice, its bounds and the typed answer; no camera."""
    return confirm((SYNTHETIC_NOTICE,), f"{seconds} seconds, {hz} updates a second, no camera.", stdin=stdin,
                   output=output)
