"""The marker observer's scene precondition, and statistics that describe it; counts only, never pixels.

``locate_copies`` (``sightcircuit.hud.observation``) applies the precondition to
every frame before it looks for the marker. The checks run in this order, and
the first that fails rejects the frame (package O3, 2026-11-06; the derivation
of every limit is in docs/hud-qualification.md, "The O3 rule"):

1. ``observer_bright_saturation``: more than ``MAX_BRIGHT_FRACTION`` of the
   pixels are bright (grey at or above ``BRIGHT_THRESHOLD``). A copy placed at
   random touches bright structure at least that often, so above this a copy is
   more likely than not exposed to it; touching structure often defeats a copy.
2. ``observer_speck_density``: more than ``MAX_SPECKS_PER_MEGAPIXEL`` specks,
   bright 8-connected components no larger than ``SPECK_EXTENT_PX`` square. A
   speck can never be a candidate quad, but one beside a copy's corner can
   still break the copy.
3. ``observer_contour_budget``: more than ``MAX_CONTOURS`` bright contours, a
   compute bound counted exactly from connected components before any contour
   is traced (``scene_census``). ``locate_copies`` then checks that the traced
   count equals the census and otherwise rejects
   (``observer_contour_census_mismatch``, with no statistics).
4. ``observer_quad_budget``: more than ``MAX_QUADS`` candidate quads (contour
   area at least ``MIN_QUAD_AREA`` that simplifies to a convex quadrilateral).
5. ``observer_outline_budget``: more than ``MAX_OUTLINES`` outline candidates,
   the quads the matcher would warp (``outline_eligible``), before any warp.

``scene_census``, ``candidate_quads`` and ``outline_eligible`` are those
steps, shared by ``locate_copies`` and ``scene_statistics`` so the statistics
describe exactly the precondition the observer applies.

``scene_statistics`` reports, for one native BGR image, every quantity the
checks use and which check, if any, rejects it (schema 2). It holds no pixel
data. ``validate_scene_statistics`` is its contract, used by the observer's
worker report contract. It also accepts the schema-1 statistics of reports
stored before O3: each schema is checked against the budgets it declares,
which must equal that schema's frozen table (``SCHEMA_BUDGETS``).

``read_still`` and ``analyze_still`` run the same statistics on one saved still
(``scripts/hud_marker_observer.py --analyze-still``). They read one regular
file, and they never discover or open the mirror, a camera or the bridge. A
path under ``/dev``, ``/proc`` or ``/sys``, a link, a FIFO or any other
non-regular file is refused before it is opened. The report tells only a PNG
from a JPEG. A PNG is lossless, but it equals a native mirror read only when
its SHA-256, width and height match a ``mirror_recognize.py --still`` capture
report; nothing here checks that, so ``provenance_verified`` is always false
and a resized PNG is not detected. A JPEG only approximates a mirror read.
"""
import hashlib
import math
import os
import stat
import struct

from ..gates import claims

BRIGHT_THRESHOLD = 180            # Grey level at or above which a pixel is bright.
MAX_BRIGHT_FRACTION = (1, 2)      # Bright pixels / all pixels above this rejects; kept as an exact ratio.
SPECK_EXTENT_PX = 5               # A bright component inside a box this size is a speck (contour area <= 16).
MAX_SPECKS_PER_MEGAPIXEL = 1700   # Specks per 10**6 pixels above this rejects.
MAX_CONTOURS = 4096               # Bright contours above this rejects, before any is traced.
MIN_QUAD_AREA = 25                # ``cv2.contourArea`` below which a contour is never a candidate quad.
MAX_QUADS = 256                   # Candidate quads above this rejects.
OUTLINE_MIN_EDGE_PX = 32          # The matcher warps a quad only if its shortest edge is at least this
OUTLINE_LONG_EDGE_PX = 80         # and its longest edge is at least this.
MAX_OUTLINES = 16                 # Outline candidates above this rejects, before any warp.
STATISTICS_MAX_CONTOURS = 16384   # Statistics trace contours (bands, quads, outlines) only up to this many.
MAX_PIXELS = 8 * 1024 * 1024
# Half-open ``cv2.contourArea`` bands [low, high): the first is what the quad filter discards.
AREA_BANDS = (("below_25", 0, 25), ("from_25_below_400", 25, 400),
              ("from_400_below_10000", 400, 10000), ("from_10000", 10000, None))
PERCENTILES = (50, 90, 99)
SCHEMA_VERSION = 2
STATISTICS_SCOPE = "Counts of one frame's bright-contour precondition; no pixel data"
# The checks in order; a frame's rejection is the first that fails.
CHECKS = (("bright_saturated", "observer_bright_saturation"),
          ("speck_density_exceeded", "observer_speck_density"),
          ("contour_budget_exceeded", "observer_contour_budget"),
          ("quad_budget_exceeded", "observer_quad_budget"),
          ("outline_budget_exceeded", "observer_outline_budget"))
REJECTIONS = tuple(reason for _, reason in CHECKS)
# The budgets each statistics schema was produced under; a report must declare exactly its schema's.
SCHEMA_BUDGETS = {
    1: {"max_contours": 2048, "max_quads": 256, "min_quad_area": 25},
    2: {"max_bright_fraction": list(MAX_BRIGHT_FRACTION), "speck_extent_px": SPECK_EXTENT_PX,
        "max_specks_per_megapixel": MAX_SPECKS_PER_MEGAPIXEL, "max_contours": MAX_CONTOURS,
        "min_quad_area": MIN_QUAD_AREA, "max_quads": MAX_QUADS, "outline_min_edge_px": OUTLINE_MIN_EDGE_PX,
        "outline_long_edge_px": OUTLINE_LONG_EDGE_PX, "max_outlines": MAX_OUTLINES,
        "statistics_max_contours": STATISTICS_MAX_CONTOURS},
}
STATISTICS_FIELDS = {
    1: ("schema_version", "scope", "width", "height", "pixels", "bright_threshold", "bright_pixel_fraction",
        "gray_percentiles", "contour_count", "contour_area_bands", "quad_count", "budgets", "precondition"),
    2: ("schema_version", "scope", "width", "height", "pixels", "bright_threshold", "bright_pixel_count",
        "bright_pixel_fraction", "gray_percentiles", "census", "contour_count", "contour_scan",
        "contour_area_bands", "quad_count", "outline_count", "budgets", "precondition"),
}
CENSUS_FIELDS = ("bright_components", "specks", "enclosed_holes")
CONTOUR_SCANS = ("complete", "skipped_over_statistics_limit")

MAX_STILL_BYTES = 64 * 1024 * 1024
REFUSED_ROOTS = ("/dev", "/proc", "/sys")
ANALYSIS_KIND = "hud_observer_scene_analysis"
ANALYSIS_SCOPE = ("File-only precondition statistics of one saved still; no device, mirror or bridge is opened, "
                  "no image is written, and nothing here qualifies a mapping")


def scene_census(gray):
    """Bright pixels, bright components, specks and enclosed holes of one grey frame, with nothing traced.

    ``contour_count`` = bright 8-connected components + dark 4-connected
    components that do not touch the frame edge. That is exactly the number of
    contours ``bright_contours`` would trace (``cv2.RETR_LIST``: one outer
    border per bright component, one hole border per enclosed dark component),
    so the contour budget is checked before any contour is built.
    """
    import cv2
    import numpy as np
    bright = (gray >= BRIGHT_THRESHOLD).astype(np.uint8)
    bright_pixels = int(cv2.countNonZero(bright))
    census = {"bright_pixels": bright_pixels, "bright_components": 0, "specks": 0, "enclosed_holes": 0}
    if bright_pixels:
        count, _, stats, _ = cv2.connectedComponentsWithStats(bright, connectivity=8)
        widths, heights = stats[1:, cv2.CC_STAT_WIDTH], stats[1:, cv2.CC_STAT_HEIGHT]
        census["bright_components"] = int(count - 1)
        census["specks"] = int(np.count_nonzero((widths <= SPECK_EXTENT_PX) & (heights <= SPECK_EXTENT_PX)))
        if bright_pixels < gray.size:
            dark_count, labels = cv2.connectedComponents(1 - bright, connectivity=4)
            edge = np.concatenate((labels[0], labels[-1], labels[:, 0], labels[:, -1]))
            touching = np.unique(edge[edge != 0])
            census["enclosed_holes"] = int(dark_count - 1 - touching.size)
    census["contour_count"] = census["bright_components"] + census["enclosed_holes"]
    return census


def _precondition(bright_pixels, pixels, specks, contour_count, quad_count, outline_count, budgets):
    """Every check's flag (None when its count is unknown) and the first that fails, from ``budgets``."""
    numerator, denominator = budgets["max_bright_fraction"]
    flags = {"bright_saturated": bright_pixels * denominator > pixels * numerator,
             "speck_density_exceeded": specks * 1_000_000 > budgets["max_specks_per_megapixel"] * pixels,
             "contour_budget_exceeded": contour_count > budgets["max_contours"],
             "quad_budget_exceeded": None if quad_count is None else quad_count > budgets["max_quads"],
             "outline_budget_exceeded": None if outline_count is None else outline_count > budgets["max_outlines"]}
    flags["rejection"] = next((reason for flag, reason in CHECKS if flags[flag]), None)
    return flags


def untraced_rejection(census, pixels):
    """The first failing check of those decided before tracing: saturation, speck density, contour budget."""
    flags = _precondition(census["bright_pixels"], pixels, census["specks"], census["contour_count"],
                          None, None, SCHEMA_BUDGETS[SCHEMA_VERSION])
    return flags["rejection"]


def bright_contours(gray):
    """Every bright contour of one grey frame, exactly as the observer finds them."""
    import cv2
    import numpy as np
    contours, _ = cv2.findContours((gray >= BRIGHT_THRESHOLD).astype(np.uint8), cv2.RETR_LIST,
                                   cv2.CHAIN_APPROX_SIMPLE)
    return contours


def candidate_quads(contours):
    """``(area, quad)`` for each contour, in order: ``quad`` is its convex quadrilateral (4 x 2 float32
    corners) or None. The observer's quad filter; each contour's area is computed once."""
    import cv2
    import numpy as np
    for contour in contours:
        area = cv2.contourArea(contour)
        quad = None
        if area >= MIN_QUAD_AREA:
            poly = cv2.approxPolyDP(contour, .015 * cv2.arcLength(contour, True), True)
            if len(poly) == 4 and cv2.isContourConvex(poly):
                quad = poly[:, 0].astype(np.float32)
        yield area, quad


def candidate_quad(contour):
    """One contour's candidate quad, or None (``candidate_quads`` for a single contour)."""
    return next(candidate_quads((contour,)))[1]


def quad_edges(quad):
    """The four edge lengths of a quad, in corner order."""
    import numpy as np
    return np.linalg.norm(quad - np.roll(quad, 1, axis=0), axis=1)


def outline_eligible(edges):
    """The matcher warps a quad as a possible whole marker only when this holds; the outline budget counts them."""
    return bool(min(edges) >= OUTLINE_MIN_EDGE_PX and max(edges) >= OUTLINE_LONG_EDGE_PX)


def _nearest_rank_percentiles(gray):
    """Grey levels at ``PERCENTILES`` by nearest rank: the value at 1-based rank ceil(p * n / 100)."""
    import numpy as np
    cumulative = np.cumsum(np.bincount(gray.ravel(), minlength=256))
    total = int(cumulative[-1])
    return {f"p{p}": int(np.searchsorted(cumulative, max(1, math.ceil(p * total / 100)))) for p in PERCENTILES}


def scene_statistics(bgr, *, gray=None, census=None, contours=None):
    """Precondition statistics of one native BGR image (schema 2); ``gray``, ``census`` and ``contours``
    reuse earlier work. Contours are traced for the area bands, quads and outlines only when there are at
    most ``STATISTICS_MAX_CONTOURS``; otherwise those three are None and ``contour_scan`` says why."""
    import cv2
    import numpy as np
    if (not isinstance(bgr, np.ndarray) or bgr.dtype != np.uint8 or bgr.ndim != 3 or bgr.shape[2] != 3 or
            not 0 < bgr.shape[0] * bgr.shape[1] <= MAX_PIXELS):
        raise ValueError("observer_image_invalid")
    if gray is None:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if census is None:
        census = scene_census(gray)
    pixels = int(gray.size)
    bands = quad_count = outline_count = None
    scan = "skipped_over_statistics_limit"
    if census["contour_count"] <= STATISTICS_MAX_CONTOURS:
        scan = "complete"
        if contours is None:
            contours = bright_contours(gray)
        bands = {name: 0 for name, _, _ in AREA_BANDS}
        quad_count = outline_count = 0
        for area, quad in candidate_quads(contours):
            for name, low, high in AREA_BANDS:
                if area >= low and (high is None or area < high):
                    bands[name] += 1
                    break
            if quad is not None:
                quad_count += 1
                outline_count += outline_eligible(quad_edges(quad))
    budgets = SCHEMA_BUDGETS[SCHEMA_VERSION]
    return {"schema_version": SCHEMA_VERSION, "scope": STATISTICS_SCOPE,
            "width": int(bgr.shape[1]), "height": int(bgr.shape[0]), "pixels": pixels,
            "bright_threshold": BRIGHT_THRESHOLD, "bright_pixel_count": census["bright_pixels"],
            "bright_pixel_fraction": round(census["bright_pixels"] / pixels, 6),
            "gray_percentiles": _nearest_rank_percentiles(gray),
            "census": {key: census[key] for key in CENSUS_FIELDS}, "contour_count": census["contour_count"],
            "contour_scan": scan, "contour_area_bands": bands, "quad_count": quad_count,
            "outline_count": outline_count,
            "budgets": {key: list(value) if isinstance(value, list) else value for key, value in budgets.items()},
            "precondition": _precondition(census["bright_pixels"], pixels, census["specks"],
                                          census["contour_count"], quad_count, outline_count, budgets)}


def _count(value):
    return type(value) is int and value >= 0


def _fraction(value):
    return type(value) in (int, float) and type(value) is not bool and 0 <= value <= 1


def _valid_percentiles(percentiles):
    return (type(percentiles) is dict and set(percentiles) == {f"p{p}" for p in PERCENTILES} and
            all(type(level) is int and 0 <= level <= 255 for level in percentiles.values()) and
            sorted(percentiles.values()) == [percentiles[f"p{p}"] for p in PERCENTILES])


def _valid_bands(bands, contour_count):
    return (type(bands) is dict and set(bands) == {name for name, _, _ in AREA_BANDS} and
            all(_count(count) for count in bands.values()) and sum(bands.values()) == contour_count)


def _same_flags(actual, expected):
    """Flags equal by identity (``True``, ``False`` or ``None``, never 1 or 0) and the same rejection."""
    return (type(actual) is dict and set(actual) == set(expected) and actual["rejection"] == expected["rejection"]
            and all(actual[key] is expected[key] for key in expected if key != "rejection"))


def _validate_schema_1(value):
    """A report stored before O3: the raw contour count against its declared 2048, quads against 256."""
    budgets, bands = value["budgets"], value["contour_area_bands"]
    if (value["scope"] != STATISTICS_SCOPE or budgets != SCHEMA_BUDGETS[1] or
            not all(_count(value[key]) for key in ("width", "height", "pixels", "contour_count", "quad_count")) or
            not 0 < value["pixels"] == value["width"] * value["height"] <= MAX_PIXELS or
            value["bright_threshold"] != BRIGHT_THRESHOLD or not _fraction(value["bright_pixel_fraction"]) or
            not _valid_bands(bands, value["contour_count"]) or
            value["quad_count"] > value["contour_count"] - bands["below_25"] or
            not _valid_percentiles(value["gray_percentiles"])):
        raise ValueError("invalid_scene_statistics")
    contour_over = value["contour_count"] > budgets["max_contours"]
    quad_over = value["quad_count"] > budgets["max_quads"]
    expected = {"contour_budget_exceeded": contour_over, "quad_budget_exceeded": quad_over,
                "rejection": ("observer_contour_budget" if contour_over else
                              "observer_quad_budget" if quad_over else None)}
    if not _same_flags(value["precondition"], expected):
        raise ValueError("invalid_scene_statistics")


def _validate_schema_2(value):
    budgets, census, bands = value["budgets"], value["census"], value["contour_area_bands"]
    pixels, bright = value["pixels"], value["bright_pixel_count"]
    if (value["scope"] != STATISTICS_SCOPE or budgets != SCHEMA_BUDGETS[2] or
            not all(_count(value[key]) for key in ("width", "height", "pixels", "bright_pixel_count",
                                                     "contour_count")) or
            not 0 < pixels == value["width"] * value["height"] <= MAX_PIXELS or
            value["bright_threshold"] != BRIGHT_THRESHOLD or not bright <= pixels or
            not _fraction(value["bright_pixel_fraction"]) or
            value["bright_pixel_fraction"] != round(bright / pixels, 6) or
            not _valid_percentiles(value["gray_percentiles"]) or
            type(census) is not dict or set(census) != set(CENSUS_FIELDS) or
            not all(_count(census[key]) for key in CENSUS_FIELDS) or
            census["specks"] > census["bright_components"] or census["bright_components"] > bright or
            value["contour_count"] != census["bright_components"] + census["enclosed_holes"] or
            value["contour_scan"] not in CONTOUR_SCANS):
        raise ValueError("invalid_scene_statistics")
    # Identities every genuine frame satisfies: bright pixels imply a component and holes imply one; holes
    # are dark pixels; and a nearest-rank percentile is bright exactly when its rank falls among the bright.
    percentiles = value["gray_percentiles"]
    if ((bright == 0) != (census["bright_components"] == 0) or census["enclosed_holes"] > pixels - bright or
            (census["enclosed_holes"] > 0 and census["bright_components"] == 0) or
            any((percentiles[f"p{p}"] >= BRIGHT_THRESHOLD) != (pixels - bright < max(1, math.ceil(p * pixels / 100)))
                for p in PERCENTILES)):
        raise ValueError("invalid_scene_statistics")
    if value["contour_scan"] == "complete":
        quads, outlines = value["quad_count"], value["outline_count"]
        if (value["contour_count"] > budgets["statistics_max_contours"] or
                not _valid_bands(bands, value["contour_count"]) or not _count(quads) or not _count(outlines) or
                quads > value["contour_count"] - bands["below_25"] or outlines > quads or
                census["specks"] > bands["below_25"]):  # a speck's contour area is at most 16
            raise ValueError("invalid_scene_statistics")
    elif (value["contour_count"] <= budgets["statistics_max_contours"] or
            (bands, value["quad_count"], value["outline_count"]) != (None, None, None)):
        raise ValueError("invalid_scene_statistics")
    expected = _precondition(bright, pixels, census["specks"], value["contour_count"], value["quad_count"],
                             value["outline_count"], budgets)
    if not _same_flags(value["precondition"], expected):
        raise ValueError("invalid_scene_statistics")


def validate_scene_statistics(value):
    """The shape of ``scene_statistics`` (schema 1 or 2): exactly its fields, counts only, internally
    consistent, with its flags recomputed from the budgets it declares, which must be its schema's own."""
    if type(value) is not dict or type(value.get("schema_version")) is not int:
        raise ValueError("invalid_scene_statistics")
    schema = value["schema_version"]
    if schema not in STATISTICS_FIELDS or set(value) != set(STATISTICS_FIELDS[schema]):
        raise ValueError("invalid_scene_statistics")
    (_validate_schema_1 if schema == 1 else _validate_schema_2)(value)


def _under_refused_root(path):
    return any(path == root or path.startswith(root + "/") for root in REFUSED_ROOTS)


def _refused_path(text):
    # The lexical check comes first, so a /proc or /dev path is refused before anything resolves it. POSIX
    # abspath keeps exactly two leading slashes ('//proc'), which Linux treats as one; collapse them.
    lexical = "/" + os.path.abspath(text).lstrip("/")
    return _under_refused_root(lexical) or _under_refused_root(os.path.realpath(text))


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
    # A PNG is a native mirror read only if it matches its --still capture report, which is not checked here.
    report.update(status="analyzed", exit_code=0, source=source, statistics=statistics, provenance_verified=False,
                  fidelity=("lossless_png_origin_unverified" if source["lossless"]
                            else "lossy_approximation_of_a_native_mirror_read"))
    return report
