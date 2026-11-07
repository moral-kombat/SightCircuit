"""Observer scene-precondition statistics and the file-only ``--analyze-still`` mode; no device access."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

try:
    import cv2
except ImportError as error:  # The dependency-free discovery jobs have no OpenCV; CI's no-skip steps do.
    raise unittest.SkipTest("OpenCV is required for observer scene tests") from error
import numpy as np

from sightcircuit.hud import observation as hud_observation
from sightcircuit.hud import scene
from scripts import hud_marker_observer as observer

ROOT = Path(__file__).resolve().parents[1]
BUDGETS = {"max_contours": 2048, "max_quads": 256, "min_quad_area": 25}
NO_REJECTION = {"contour_budget_exceeded": False, "quad_budget_exceeded": False, "rejection": None}


def grey(height, width, value):
    return np.full((height, width, 3), value, np.uint8)


def banded_frame():
    """200 x 300 grey 90 with, by ``cv2.contourArea`` (polygon through pixel centres):
    20 specks of 2 x 2 (area 1), three 10 x 10 squares (81), one 30 x 30 (841), one 110 x 110 (11881)."""
    image = grey(200, 300, 90)
    for k in range(20):
        image[5:7, 5 + 12 * k:7 + 12 * k] = 255
    for x in (10, 40, 70):
        image[20:30, x:x + 10] = 255
    image[50:80, 10:40] = 255
    image[50:160, 120:230] = 255
    return image


BANDED = {
    "schema_version": 1, "scope": scene.STATISTICS_SCOPE, "width": 300, "height": 200, "pixels": 60000,
    "bright_threshold": 180,
    "bright_pixel_fraction": 0.223,  # 80 + 300 + 900 + 12100 = 13380 of 60000
    "gray_percentiles": {"p50": 90, "p90": 255, "p99": 255},  # 46620 at 90; rank 54000 is past them
    "contour_count": 25,
    "contour_area_bands": {"below_25": 20, "from_25_below_400": 3, "from_400_below_10000": 1, "from_10000": 1},
    "quad_count": 5, "budgets": BUDGETS, "precondition": NO_REJECTION,
}


def dots(count, size, pitch, height=600, width=1000):
    """``count`` bright ``size`` x ``size`` squares, row by row at ``pitch`` pixels."""
    image = grey(height, width, 90)
    columns = (width - 2) // pitch
    for index in range(count):
        y, x = 1 + (index // columns) * pitch, 1 + (index % columns) * pitch
        image[y:y + size, x:x + size] = 255
    return image


class SceneStatisticsTests(unittest.TestCase):
    def test_area_bands_quads_fraction_and_percentiles_match_hand_counts(self):
        self.assertEqual(scene.scene_statistics(banded_frame()), BANDED)
        scene.validate_scene_statistics(BANDED)

    def test_threshold_and_quad_area_boundaries(self):
        # Grey 179 is not bright; a 5 x 5 blob at 180 has area 16 (no quad), a 6 x 6 blob has area 25 (a quad).
        image = grey(100, 100, 179)
        image[10:15, 10:15] = 180
        image[40:46, 40:46] = 180
        statistics = scene.scene_statistics(image)
        self.assertEqual(statistics["contour_count"], 2)
        self.assertEqual(statistics["contour_area_bands"],
                         {"below_25": 1, "from_25_below_400": 1, "from_400_below_10000": 0, "from_10000": 0})
        self.assertEqual(statistics["quad_count"], 1)
        self.assertEqual(statistics["bright_pixel_fraction"], 0.0061)  # 61 of 10000
        self.assertEqual(statistics["gray_percentiles"], {"p50": 179, "p90": 179, "p99": 179})

    def test_nearest_rank_percentiles(self):
        # 100 pixels holding 0..99: ranks 50, 90 and 99 hold 49, 89 and 98.
        image = np.repeat(np.arange(100, dtype=np.uint8).reshape(10, 10, 1), 3, axis=2)
        statistics = scene.scene_statistics(image)
        self.assertEqual(statistics["gray_percentiles"], {"p50": 49, "p90": 89, "p99": 98})
        self.assertEqual((statistics["contour_count"], statistics["bright_pixel_fraction"]), (0, 0.0))
        one = scene.scene_statistics(grey(1, 1, 200))
        self.assertEqual(one["gray_percentiles"], {"p50": 200, "p90": 200, "p99": 200})

    def test_budget_boundaries_are_exact(self):
        for count, exceeded in ((2048, False), (2049, True)):
            with self.subTest(contours=count):
                statistics = scene.scene_statistics(dots(count, 2, 4))
                self.assertEqual(statistics["contour_count"], count)
                self.assertEqual(statistics["precondition"]["contour_budget_exceeded"], exceeded)
                self.assertEqual(statistics["precondition"]["rejection"],
                                 "observer_contour_budget" if exceeded else None)
        for count, exceeded in ((256, False), (257, True)):
            with self.subTest(quads=count):
                statistics = scene.scene_statistics(dots(count, 10, 12))
                self.assertEqual((statistics["contour_count"], statistics["quad_count"]), (count, count))
                self.assertEqual(statistics["precondition"]["rejection"], "observer_quad_budget" if exceeded else None)

    def test_noise_grid_matches_the_observer_rejection(self):
        image = grey(600, 1000, 90)
        for y in range(5, 595, 10):
            for x in range(5, 995, 10):
                image[y:y + 2, x:x + 2] = 255
        statistics = scene.scene_statistics(image)
        self.assertEqual(statistics["contour_count"], 59 * 99)
        self.assertEqual(statistics["bright_pixel_fraction"], 0.03894)  # 4 x 5841 of 600000
        self.assertEqual(statistics["gray_percentiles"], {"p50": 90, "p90": 90, "p99": 255})
        self.assertEqual(statistics["precondition"]["rejection"], "observer_contour_budget")
        details = {}
        with self.assertRaisesRegex(ValueError, "observer_contour_budget"):
            hud_observation.locate_copies(image, diagnostics=details)
        self.assertEqual(details["scene_statistics"], statistics)
        self.assertFalse(details["scan_complete"])

    def test_statistics_are_the_observer_precondition(self):
        # The quad budget the observer applies, at the same frame, with the complete count alongside.
        image = dots(300, 10, 12)
        details = {}
        with self.assertRaisesRegex(ValueError, "observer_quad_budget"):
            hud_observation.locate_copies(image, diagnostics=details)
        self.assertEqual(details["quad_count"], scene.MAX_QUADS + 1)
        self.assertEqual(details["scene_statistics"]["quad_count"], 300)
        # A frame that passes the precondition carries no statistics in its diagnostics.
        details = {}
        hud_observation.locate_copies(banded_frame(), diagnostics=details)
        self.assertNotIn("scene_statistics", details)
        self.assertEqual(details["contour_count"], BANDED["contour_count"])
        self.assertIs(hud_observation.MAX_CONTOURS, scene.MAX_CONTOURS)
        self.assertIs(hud_observation.MAX_QUADS, scene.MAX_QUADS)

    def test_array_subclasses_keep_the_budget_rejection(self):
        image = dots(2049, 2, 4)
        with tempfile.TemporaryDirectory() as temporary:
            mapped = np.memmap(Path(temporary) / "frame", dtype=np.uint8, mode="w+", shape=image.shape)
            mapped[:] = image
            class View(np.ndarray):
                pass
            for frame in (mapped, image.view(View)):
                with self.subTest(kind=type(frame).__name__):
                    details = {}
                    with self.assertRaisesRegex(ValueError, "^observer_contour_budget$"):
                        hud_observation.locate_copies(frame, diagnostics=details)
                    self.assertEqual((details["status"], details["reason"]), ("rejected", "observer_contour_budget"))
                    self.assertEqual(details["scene_statistics"], scene.scene_statistics(image))
            del mapped

    def test_failing_statistics_cannot_replace_the_rejection(self):
        details = {}
        with patch.object(hud_observation, "scene_statistics", side_effect=MemoryError), \
                self.assertRaisesRegex(ValueError, "^observer_quad_budget$"):
            hud_observation.locate_copies(dots(257, 10, 12), diagnostics=details)
        self.assertEqual((details["status"], details["reason"]), ("rejected", "observer_quad_budget"))
        self.assertEqual(details["scene_statistics_error"], "MemoryError")
        self.assertNotIn("scene_statistics", details)

    def test_invalid_images_are_refused(self):
        for image in (np.zeros((4, 4), np.uint8), np.zeros((4, 4, 3), np.float32), np.zeros((0, 4, 3), np.uint8),
                      np.zeros((4, 4, 4), np.uint8), [[0]]):
            with self.subTest(shape=getattr(image, "shape", None)), self.assertRaises(ValueError):
                scene.scene_statistics(image)
        with patch.object(scene, "MAX_PIXELS", 99), self.assertRaises(ValueError):
            scene.scene_statistics(grey(10, 10, 0))


def png_bytes(image):
    okay, data = cv2.imencode(".png", image)
    assert okay
    return data.tobytes()


def png_header(width, height):
    """A PNG signature and IHDR claiming ``width`` x ``height``, then nothing decodable."""
    body = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunk = struct.pack(">I", len(body)) + b"IHDR" + body + struct.pack(">I", zlib.crc32(b"IHDR" + body))
    return b"\x89PNG\r\n\x1a\n" + chunk


class AnalyzeStillTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, name, data):
        path = self.root / name
        path.write_bytes(data)
        return path

    def run_main(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = observer.main(list(arguments))
        return code, json.loads(output.getvalue())

    @contextlib.contextmanager
    def no_device_or_bridge(self):
        """Fail on any discovery, capture, bridge or marker call, and record every ``os.open`` path."""
        opened = []
        real_open = os.open
        def recording_open(path, *args, **kwargs):
            opened.append(os.fspath(path))
            return real_open(path, *args, **kwargs)
        forbidden = AssertionError("device, mirror or bridge touched")
        with patch.object(observer, "discover", side_effect=forbidden), \
                patch.object(observer, "select_candidate", side_effect=forbidden), \
                patch.object(observer, "capture", side_effect=forbidden), \
                patch.object(observer, "supervise", side_effect=forbidden), \
                patch.object(observer, "MarkerClient", side_effect=forbidden), \
                patch.object(scene.os, "open", side_effect=recording_open):
            yield opened
        self.assertFalse([path for path in opened if path.startswith(("/dev", "/proc", "/sys"))], opened)

    def test_png_still_reports_exact_statistics_and_its_source(self):
        data = png_bytes(banded_frame())
        path = self.write("still.png", data)
        with self.no_device_or_bridge() as opened:
            code, report = self.run_main("--analyze-still", str(path))
        self.assertEqual(opened, [str(path)])
        self.assertEqual((code, report["status"], report["exit_code"]), (0, "analyzed", 0))
        self.assertEqual(report["report_kind"], "hud_observer_scene_analysis")
        self.assertEqual(report["statistics"], BANDED)
        self.assertEqual(report["source"], {"format": "png", "lossless": True, "bytes": len(data),
                                            "sha256": hashlib.sha256(data).hexdigest(), "width": 300, "height": 200})
        self.assertEqual(report["fidelity"], "native_lossless_if_captured_by_mirror_recognize_still")
        for key in ("device_opened", "images_saved", "mapping_qualified", "production_live_input_enabled",
                    "production_hud_enabled"):
            self.assertIs(report[key], False, key)

    def test_rejecting_scene_is_still_an_analysis_not_a_failure(self):
        path = self.write("noise.png", png_bytes(dots(2049, 2, 4)))
        code, report = self.run_main("--analyze-still", str(path))
        self.assertEqual(code, 0)
        self.assertEqual(report["statistics"]["precondition"]["rejection"], "observer_contour_budget")

    def test_jpeg_fill_bytes_before_a_marker_are_skipped(self):
        # FF D8, two fill bytes, then SOF0 (length 17, 8-bit, 64 high, 96 wide).
        data = b"\xff\xd8" + b"\xff\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", 64, 96) + bytes(10)
        self.assertEqual(scene._header(data), ("jpeg", 96, 64))

    def test_a_decode_failure_is_a_refusal_not_a_traceback(self):
        still = self.write("still.png", png_bytes(banded_frame()))
        with patch("cv2.imdecode", side_effect=RuntimeError("boom")):
            report = scene.analyze_still(str(still))
        self.assertEqual((report["status"], report["exit_code"]), ("refused", 1))
        self.assertEqual(report["reason"], "observer_still_analysis_error:RuntimeError")

    def test_jpeg_still_is_marked_lossy(self):
        okay, data = cv2.imencode(".jpg", banded_frame(), [cv2.IMWRITE_JPEG_QUALITY, 95])
        self.assertTrue(okay)
        code, report = self.run_main("--analyze-still", str(self.write("still.jpg", data.tobytes())))
        self.assertEqual(code, 0)
        self.assertEqual((report["source"]["format"], report["source"]["lossless"]), ("jpeg", False))
        self.assertEqual((report["source"]["width"], report["source"]["height"]), (300, 200))
        self.assertEqual(report["fidelity"], "lossy_approximation_of_a_native_mirror_read")
        scene.validate_scene_statistics(report["statistics"])

    def test_device_proc_sys_links_fifos_and_directories_are_refused_before_opening(self):
        target = self.write("real.png", png_bytes(banded_frame()))
        link_to_device = self.root / "camera.png"
        link_to_device.symlink_to("/dev/null")
        link_to_file = self.root / "link.png"
        link_to_file.symlink_to(target)
        fifo = self.root / "pipe.png"
        os.mkfifo(fifo)
        cases = [("/dev/null", "observer_still_device_path_refused"),
                 ("/dev/video99", "observer_still_device_path_refused"),
                 ("/dev/shm/frames.png", "observer_still_device_path_refused"),
                 ("/proc/self/status", "observer_still_device_path_refused"),
                 ("/sys/kernel/notes", "observer_still_device_path_refused"),
                 ("/dev/../dev/null", "observer_still_device_path_refused"),
                 (str(link_to_device), "observer_still_device_path_refused"),
                 (str(link_to_file), "observer_still_symlink_refused"),
                 (str(fifo), "observer_still_not_regular_file"),
                 (str(self.root), "observer_still_not_regular_file"),
                 (str(self.root / "missing.png"), "observer_still_unreadable")]
        for path, reason in cases:
            with self.subTest(path=path), self.no_device_or_bridge() as opened:
                code, report = self.run_main("--analyze-still", path)
                self.assertEqual((code, report["status"], report["exit_code"], report["reason"]),
                                 (1, "refused", 1, reason))
                self.assertEqual(opened, [])  # Refused before any open: no device node or FIFO is opened.
                self.assertNotIn("statistics", report)
                self.assertIs(report["device_opened"], False)

    def test_bytes_paths_are_checked_like_text(self):
        data = png_bytes(banded_frame())
        path = self.write("still.png", data)
        report = scene.analyze_still(os.fsencode(path))
        self.assertEqual((report["status"], report["statistics"]), ("analyzed", BANDED))
        with self.no_device_or_bridge() as opened:
            report = scene.analyze_still(b"/dev/null")
        self.assertEqual((report["status"], report["reason"]), ("refused", "observer_still_device_path_refused"))
        self.assertEqual(opened, [])

    def test_unusable_files_are_refused(self):
        cases = [(self.write("text.png", b"not an image at all"), "observer_still_format_unsupported"),
                 (self.write("empty.png", b""), "observer_still_format_unsupported"),
                 (self.write("huge.png", png_header(10000, 10000)), "observer_pixel_budget"),
                 (self.write("zero.png", png_header(0, 10)), "observer_pixel_budget"),
                 (self.write("broken.png", png_header(64, 64) + b"\x00" * 64), "observer_still_undecodable"),
                 (self.write("cut.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"), "observer_still_format_unsupported")]
        for path, reason in cases:
            with self.subTest(path=path.name):
                code, report = self.run_main("--analyze-still", str(path))
                self.assertEqual((code, report["reason"]), (1, reason))
        large = self.write("large.png", png_bytes(banded_frame()))
        with patch.object(scene, "MAX_STILL_BYTES", 100):
            code, report = self.run_main("--analyze-still", str(large))
        self.assertEqual((code, report["reason"]), (1, "observer_still_too_large"))

    def test_file_swapped_while_opening_is_refused(self):
        first = self.write("first.png", png_bytes(banded_frame()))
        second = self.write("second.png", png_bytes(banded_frame()))
        real_lstat = os.lstat
        with patch.object(scene.os, "lstat", side_effect=lambda path: real_lstat(second)):
            code, report = self.run_main("--analyze-still", str(first))
        self.assertEqual((code, report["reason"]), (1, "observer_still_changed_while_opening"))

    def test_analysis_takes_no_observation_device_bridge_or_session_option(self):
        path = str(self.write("still.png", png_bytes(banded_frame())))
        for extra in (["--observe-calibration"], ["--allow-mirror-observation"], ["--allow-calibration-output"],
                      ["--device", "/dev/video99"], ["--bridge", "bridge"], ["--record-session-tokens"]):
            with self.subTest(extra=extra), self.no_device_or_bridge(), \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                observer.main(["--analyze-still", path, *extra])
            self.assertEqual(raised.exception.code, 2)

    def test_runs_as_a_file_and_prints_one_json_report(self):
        path = self.write("still.png", png_bytes(banded_frame()))
        result = subprocess.run([sys.executable, str(ROOT / "scripts/hud_marker_observer.py"),
                                 "--analyze-still", str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["statistics"], BANDED)


if __name__ == "__main__":
    unittest.main()
