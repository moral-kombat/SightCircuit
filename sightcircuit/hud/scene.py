"""The marker observer's scene precondition, and statistics that describe it; counts only, never pixels.

``locate_copies`` (``sightcircuit.hud.observation``) thresholds the grey frame
at ``BRIGHT_THRESHOLD``, finds every bright contour, rejects the frame when
there are more than ``MAX_CONTOURS``, then keeps contours of at least
``MIN_QUAD_AREA`` (``cv2.contourArea``: the polygon through pixel centres, so a
5 x 5 blob has area 16) that simplify to a convex quadrilateral, rejecting
more than ``MAX_QUADS``. ``bright_contours`` and ``candidate_quad`` are those
two steps, shared by ``locate_copies`` and ``scene_statistics`` so the
statistics describe exactly the precondition the observer applies.

``scene_statistics`` reports, for one native BGR image: the contour count and
its split into area bands, the share of pixels at or above the threshold, grey
percentiles (nearest rank), the full candidate-quad count and which budget, if
any, rejects the frame. It holds no pixel data. ``validate_scene_statistics``
is its contract, used by the observer's worker report contract.

``read_still`` and ``analyze_still`` run the same statistics on one saved still
(``scripts/hud_marker_observer.py --analyze-still``). They read one regular
file, and they never discover or open the mirror, a camera or the bridge. A
path under ``/dev``, ``/proc`` or ``/sys``, a link, a FIFO or any other
non-regular file is refused before it is opened. A PNG still written by
``mirror_recognize.py --still`` holds the native mirror frame losslessly, so
its statistics equal the observer's on that frame; a JPEG or resized still
only approximates it, and the report says which.
"""
import hashlib
import math
import os
import stat
import struct

from ..gates import claims

BRIGHT_THRESHOLD = 180    # Grey level at or above which a pixel is bright.
MIN_QUAD_AREA = 25        # ``cv2.contourArea`` below which a contour is never a candidate quad.
MAX_CONTOURS = 2048       # Bright contours a frame may hold; more rejects it (``observer_contour_budget``).
MAX_QUADS = 256           # Candidate quads a frame may hold; more rejects it (``observer_quad_budget``).
MAX_PIXELS = 8 * 1024 * 1024
# Half-open ``cv2.contourArea`` bands [low, high): the first is what the quad filter discards.
AREA_BANDS = (("below_25", 0, 25), ("from_25_below_400", 25, 400),
              ("from_400_below_10000", 400, 10000), ("from_10000", 10000, None))
PERCENTILES = (50, 90, 99)
STATISTICS_FIELDS = ("schema_version", "scope", "width", "height", "pixels", "bright_threshold",
                     "bright_pixel_fraction", "gray_percentiles", "contour_count", "contour_area_bands",
                     "quad_count", "budgets", "precondition")
STATISTICS_SCOPE = "Counts of one frame's bright-contour precondition; no pixel data"
REJECTIONS = ("observer_contour_budget", "observer_quad_budget")

MAX_STILL_BYTES = 64 * 1024 * 1024
REFUSED_ROOTS = ("/dev", "/proc", "/sys")
ANALYSIS_KIND = "hud_observer_scene_analysis"
ANALYSIS_SCOPE = ("File-only precondition statistics of one saved still; no device, mirror or bridge is opened, "
                  "no image is written, and nothing here qualifies a mapping")


def bright_contours(gray):
    """Every bright contour of one grey frame, exactly as the observer finds them."""
    import cv2
    import numpy as np
    contours, _ = cv2.findContours((gray >= BRIGHT_THRESHOLD).astype(np.uint8), cv2.RETR_LIST,
                                   cv2.CHAIN_APPROX_SIMPLE)
    return contours


def candidate_quad(contour):
    """The contour's convex quadrilateral (4 x 2 float32 corners), or None: the observer's quad filter."""
    import cv2
    import numpy as np
    if cv2.contourArea(contour) < MIN_QUAD_AREA:
        return None
    poly = cv2.approxPolyDP(contour, .015 * cv2.arcLength(contour, True), True)
    if len(poly) == 4 and cv2.isContourConvex(poly):
        return poly[:, 0].astype(np.float32)
    return None


def _nearest_rank_percentiles(gray):
    """Grey levels at ``PERCENTILES`` by nearest rank: the value at 1-based rank ceil(p * n / 100)."""
    import numpy as np
    cumulative = np.cumsum(np.bincount(gray.ravel(), minlength=256))
    total = int(cumulative[-1])
    return {f"p{p}": int(np.searchsorted(cumulative, max(1, math.ceil(p * total / 100)))) for p in PERCENTILES}


def scene_statistics(bgr, *, gray=None, contours=None):
    """Precondition statistics of one native BGR image; ``gray`` and ``contours`` reuse earlier work."""
    import cv2
    import numpy as np
    if (not isinstance(bgr, np.ndarray) or bgr.dtype != np.uint8 or bgr.ndim != 3 or bgr.shape[2] != 3 or
            not 0 < bgr.shape[0] * bgr.shape[1] <= MAX_PIXELS):
        raise ValueError("observer_image_invalid")
    if gray is None:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if contours is None:
        contours = bright_contours(gray)
    bands = {name: 0 for name, _, _ in AREA_BANDS}
    quads = 0
    for contour in contours:
        area = cv2.contourArea(contour)
        for name, low, high in AREA_BANDS:
            if area >= low and (high is None or area < high):
                bands[name] += 1
                break
        quads += candidate_quad(contour) is not None
    pixels = int(gray.size)
    bright = int(np.count_nonzero(gray >= BRIGHT_THRESHOLD))
    contour_over = len(contours) > MAX_CONTOURS
    quad_over = quads > MAX_QUADS
    return {"schema_version": 1, "scope": STATISTICS_SCOPE,
            "width": int(bgr.shape[1]), "height": int(bgr.shape[0]), "pixels": pixels,
            "bright_threshold": BRIGHT_THRESHOLD, "bright_pixel_fraction": round(bright / pixels, 6),
            "gray_percentiles": _nearest_rank_percentiles(gray),
            "contour_count": len(contours), "contour_area_bands": bands, "quad_count": quads,
            "budgets": {"max_contours": MAX_CONTOURS, "max_quads": MAX_QUADS, "min_quad_area": MIN_QUAD_AREA},
            # The observer checks contours first and stops there; the quad count is still complete here.
            "precondition": {"contour_budget_exceeded": contour_over, "quad_budget_exceeded": quad_over,
                             "rejection": REJECTIONS[0] if contour_over else REJECTIONS[1] if quad_over else None}}


def _count(value):
    return type(value) is int and value >= 0


def validate_scene_statistics(value):
    """The shape of ``scene_statistics``: exactly its fields, counts only, internally consistent."""
    if type(value) is not dict or set(value) != set(STATISTICS_FIELDS):
        raise ValueError("invalid_scene_statistics")
    bands, budgets, precondition, percentiles = (value["contour_area_bands"], value["budgets"],
                                                 value["precondition"], value["gray_percentiles"])
    fraction = value["bright_pixel_fraction"]
    if (value["schema_version"] != 1 or value["scope"] != STATISTICS_SCOPE or
            not all(_count(value[key]) for key in ("width", "height", "pixels", "contour_count", "quad_count")) or
            not 0 < value["pixels"] == value["width"] * value["height"] <= MAX_PIXELS or
            value["bright_threshold"] != BRIGHT_THRESHOLD or
            type(fraction) not in (int, float) or type(fraction) is bool or not 0 <= fraction <= 1 or
            type(bands) is not dict or set(bands) != {name for name, _, _ in AREA_BANDS} or
            not all(_count(count) for count in bands.values()) or sum(bands.values()) != value["contour_count"] or
            value["quad_count"] > value["contour_count"] - bands["below_25"] or
            budgets != {"max_contours": MAX_CONTOURS, "max_quads": MAX_QUADS, "min_quad_area": MIN_QUAD_AREA} or
            type(percentiles) is not dict or set(percentiles) != {f"p{p}" for p in PERCENTILES} or
            not all(type(level) is int and 0 <= level <= 255 for level in percentiles.values()) or
            sorted(percentiles.values()) != [percentiles[f"p{p}"] for p in PERCENTILES]):
        raise ValueError("invalid_scene_statistics")
    contour_over = value["contour_count"] > MAX_CONTOURS
    quad_over = value["quad_count"] > MAX_QUADS
    expected = {"contour_budget_exceeded": contour_over, "quad_budget_exceeded": quad_over,
                "rejection": REJECTIONS[0] if contour_over else REJECTIONS[1] if quad_over else None}
    if precondition != expected:
        raise ValueError("invalid_scene_statistics")


def _refused_path(text):
    resolved = os.path.realpath(text)
    return any(path == root or path.startswith(root + "/") for path in (os.path.abspath(text), resolved)
               for root in REFUSED_ROOTS)


def _header(data):
    """``(format, width, height)`` from a PNG or JPEG header, read before any decode."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24 and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        return "png", width, height
    if data[:2] == b"\xff\xd8":
        offset = 2
        while offset + 4 <= len(data):
            if data[offset] != 0xFF:
                break
            while offset + 1 < len(data) and data[offset + 1] == 0xFF:
                offset += 1  # fill bytes before a marker
            if offset + 4 > len(data):
                break
            marker = data[offset + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                offset += 2
                continue
            length = struct.unpack(">H", data[offset + 2:offset + 4])[0]
            # Start-of-frame markers, excluding DHT (C4), JPG (C8) and DAC (CC).
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC) and offset + 9 <= len(data):
                height, width = struct.unpack(">HH", data[offset + 5:offset + 9])
                return "jpeg", width, height
            if length < 2:
                break
            offset += 2 + length
    raise ValueError("observer_still_format_unsupported")


def read_still(path):
    """Decode one saved PNG or JPEG still as native BGR; refuses anything but a bounded regular file.

    The path is refused before it is opened when it, or what it resolves to,
    lies under /dev, /proc or /sys, when it is a symbolic link, or when it is
    not a regular file. The open itself uses ``O_NOFOLLOW`` and ``O_NONBLOCK``,
    and the opened file must be the same regular file that was checked;
    otherwise it is closed unread. Only a parent directory swapped by another
    process between that check and the open could make the open reach a
    different file. Returns the image and a ``source`` record: format, byte
    count, SHA-256 and pixel size.
    """
    import cv2
    import numpy as np
    text = os.fsdecode(path)  # A bytes path is checked as text, like any other.
    if _refused_path(text):
        raise ValueError("observer_still_device_path_refused")
    try:
        before = os.lstat(text)
    except OSError as error:
        raise ValueError("observer_still_unreadable") from error
    if stat.S_ISLNK(before.st_mode):
        raise ValueError("observer_still_symlink_refused")
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("observer_still_not_regular_file")
    if before.st_size > MAX_STILL_BYTES:
        raise ValueError("observer_still_too_large")
    try:
        descriptor = os.open(text, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as error:
        raise ValueError("observer_still_unreadable") from error
    try:
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError("observer_still_changed_while_opening")
        chunks, size = [], 0
        while size <= MAX_STILL_BYTES:
            chunk = os.read(descriptor, min(1 << 20, MAX_STILL_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    finally:
        os.close(descriptor)
    data = b"".join(chunks)
    if len(data) > MAX_STILL_BYTES:
        raise ValueError("observer_still_too_large")
    fmt, width, height = _header(data)
    if not 0 < width * height <= MAX_PIXELS:
        raise ValueError("observer_pixel_budget")
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
    if image is None or image.shape[:2] != (height, width) or image.dtype != np.uint8:
        raise ValueError("observer_still_undecodable")
    source = {"format": fmt, "lossless": fmt == "png", "bytes": len(data),
              "sha256": hashlib.sha256(data).hexdigest(), "width": width, "height": height}
    return np.ascontiguousarray(image), source


def new_analysis_report():
    return {"report_kind": ANALYSIS_KIND, "schema_version": 1, "status": "not_run", "exit_code": 3,
            "scope": ANALYSIS_SCOPE, "device_opened": False, "images_saved": False, **claims()}


def analyze_still(path):
    """The ``--analyze-still`` report for one still: exit 0 with statistics, or exit 1 with the refusal."""
    report = new_analysis_report()
    try:
        image, source = read_still(path)
        statistics = scene_statistics(image)
    except ValueError as error:
        report.update(status="refused", exit_code=1, reason=str(error)[:256])
        return report
    except Exception as error:  # cv2.error, MemoryError: the exit contract holds, with the type named
        report.update(status="refused", exit_code=1,
                      reason=f"observer_still_analysis_error:{type(error).__name__}"[:256])
        return report
    report.update(status="analyzed", exit_code=0, source=source, statistics=statistics,
                  fidelity=("native_lossless_if_captured_by_mirror_recognize_still" if source["lossless"]
                            else "lossy_approximation_of_a_native_mirror_read"))
    return report
