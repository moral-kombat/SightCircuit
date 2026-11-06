"""Run-context sensor sampler against fake /proc and /sys trees (no device)."""
import ast
import contextlib
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from framejacker.runtime import sensors

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/sample_run_context.py"
WALL_NS = 1_800_000_000_123_456_789
MONOTONIC_NS = 987_654_321


def fixed_clock():
    return WALL_NS, MONOTONIC_NS


def write(root, relative, text):
    path = Path(root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def frame_like_tree(root):
    """A fake tree shaped like upstream's SteamOS 0.4.5 Frame paths, with gaps."""
    for index, (kind, temp) in enumerate((("cpu-0-0", "45000"), ("cpu-1-0", "52500"), ("cpuss-0", "99000"),
                                          ("gpuss-0", "61000"), ("ddr", "40000"))):
        write(root, f"sys/class/thermal/thermal_zone{index}/type", kind)
        write(root, f"sys/class/thermal/thermal_zone{index}/temp", temp)
    # A cpu zone whose temp file is missing: skipped by the maximum, not -999.
    write(root, "sys/class/thermal/thermal_zone9/type", "cpu-7-0")
    for index, (kind, state) in enumerate((("cpufreq-cpu0", "0"), ("cpufreq-cpu4", "2"),
                                           ("devfreq-3d00000.gpu", "1"))):
        write(root, f"sys/class/thermal/cooling_device{index}/type", kind)
        write(root, f"sys/class/thermal/cooling_device{index}/cur_state", state)
    write(root, "sys/class/hwmon/hwmon0/name", "max34417")
    for index, (label, microwatts) in enumerate((("gpu", "1250000"), ("vph", "2500000"), ("unused", "7"))):
        write(root, f"sys/class/hwmon/hwmon0/power{index + 1}_input", microwatts)
        write(root, f"sys/class/hwmon/hwmon0/power{index + 1}_label", label)
    write(root, "sys/class/hwmon/hwmon1/name", "other")
    write(root, "sys/class/hwmon/hwmon1/power1_input", "999")
    for policy, current, cap in (("policy0", "1800000", "2000000"), ("policy4", "2400000", "3000000"),
                                 ("policy10", None, "3200000")):
        if current is not None:
            write(root, f"sys/devices/system/cpu/cpufreq/{policy}/scaling_cur_freq", current)
        write(root, f"sys/devices/system/cpu/cpufreq/{policy}/scaling_max_freq", cap)
    write(root, "sys/class/devfreq/3d00000.gpu/cur_freq", "587000000")
    battery = "sys/class/power_supply/battery"
    for name, value in (("type", "Battery"), ("capacity", "81"), ("status", "Discharging"),
                        ("voltage_now", "4000000"), ("current_now", "-500000"), ("temp", "312")):
        write(root, f"{battery}/{name}", value)
    write(root, "sys/class/power_supply/pmi-charger/type", "USB")
    write(root, "sys/class/power_supply/pmi-charger/online", "0")
    write(root, "sys/bus/iio/devices/iio:device0/name", sensors.VADC_NAME)
    write(root, "sys/bus/iio/devices/iio:device0/in_temp_display_left_therm_input", "33000")
    write(root, "sys/bus/iio/devices/iio:device0/in_temp_pcba_at_heat_sink_fins_input", "38500")
    write(root, "proc/stat", "cpu  100 0 100 700 100 0 0 0 0 0\ncpu0 1 2 3 4 5\n")
    write(root, "proc/meminfo", "MemTotal: 16000000 kB\nMemAvailable:    2048000 kB\n")


EXPECTED_COLUMNS = [
    "utc", "monotonic_ns", "cpu_util_pct", "cpu_temp_max", "gpu_temp_max", "ddr_temp",
    "skin_display", "skin_exhaust", "skin_heatsink", "bat_temp", "throttle_cpu", "throttle_gpu",
    "policy0_mhz", "policy4_mhz", "policy10_mhz", "policy0_cap_mhz", "policy4_cap_mhz", "policy10_cap_mhz",
    "gpu_mhz", "w_vph", "w_gpu", "bat_pct", "bat_status", "bat_w", "charger_online", "mem_avail_mb",
]
# Hand-computed from frame_like_tree, with /proc/stat advanced by 800 jiffies, 600 of them idle.
EXPECTED_ROW = [
    "2027-01-15T08:00:00.123+00:00", "987654321", "25.00", "52.50", "61.00", "40.00",
    "33.00", "", "38.50", "31.20", "2", "1",
    "1800.00", "2400.00", "", "2000.00", "3000.00", "3200.00",
    "587.00", "2.50", "1.25", "81.00", "Discharging", "-2.00", "0", "2000",
]
MINIMAL_COLUMNS = ["utc", "monotonic_ns", "cpu_util_pct", "cpu_temp_max", "gpu_temp_max", "ddr_temp",
                   "skin_display", "skin_exhaust", "skin_heatsink", "bat_temp", "throttle_cpu", "throttle_gpu",
                   "gpu_mhz", "bat_pct", "bat_status", "bat_w", "charger_online", "mem_avail_mb"]


class SensorSamplerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def csv_rows(self, sampler, samples=1, advance=True):
        output = io.StringIO()
        def sleep(_seconds):
            if advance:
                write(self.root, "proc/stat", "cpu  200 0 200 1300 100 0 0 0 0 0\n")
        self.assertEqual(sensors.run(sampler, output, interval=.5, samples=samples, sleep=sleep), samples)
        return list(csv.reader(io.StringIO(output.getvalue())))

    def test_frame_like_tree_gives_the_hand_computed_row(self):
        frame_like_tree(self.root)
        sampler = sensors.SensorSampler(self.root, clock=fixed_clock)
        rows = self.csv_rows(sampler)
        self.assertEqual(rows[0], EXPECTED_COLUMNS)
        self.assertEqual(rows[1], EXPECTED_ROW)
        self.assertEqual(len(rows), 2)

    def test_no_wifi_columns_and_no_sentinel(self):
        frame_like_tree(self.root)
        rows = self.csv_rows(sensors.SensorSampler(self.root, clock=fixed_clock))
        self.assertFalse([c for c in rows[0] if "wifi" in c or "dongle" in c])
        self.assertNotIn("-999", ",".join(rows[1]))

    def test_empty_tree_gives_blank_readings_but_both_clocks(self):
        rows = self.csv_rows(sensors.SensorSampler(self.root, clock=fixed_clock), advance=False)
        self.assertEqual(rows[0], MINIMAL_COLUMNS)
        self.assertEqual(rows[1], ["2027-01-15T08:00:00.123+00:00", "987654321"] + [""] * (len(MINIMAL_COLUMNS) - 2))

    def test_unreadable_and_malformed_values_are_blank(self):
        frame_like_tree(self.root)
        for relative in ("sys/class/thermal/thermal_zone3/temp", "sys/class/thermal/cooling_device1/cur_state",
                         "sys/class/power_supply/battery/voltage_now", "sys/class/devfreq/3d00000.gpu/cur_freq"):
            write(self.root, relative, "not-a-number")
        write(self.root, "sys/class/thermal/cooling_device0/cur_state", "")
        write(self.root, "proc/meminfo", "MemTotal: 1 kB\n")
        sampler = sensors.SensorSampler(self.root, clock=fixed_clock)
        write(self.root, "proc/stat", "garbage\n")  # unreadable second tick reading
        row = dict(zip(*self.csv_rows(sampler, advance=False)))
        for column in ("cpu_util_pct", "gpu_temp_max", "bat_w", "gpu_mhz", "mem_avail_mb"):
            self.assertEqual(row[column], "", column)
        self.assertEqual(row["throttle_cpu"], "")  # neither cpu cooling state readable: blank, not 0
        self.assertEqual(row["throttle_gpu"], "1")
        self.assertEqual(row["cpu_temp_max"], "52.50")

    def test_one_clock_reading_per_row(self):
        frame_like_tree(self.root)
        readings = iter([(WALL_NS, 1), (WALL_NS + 5_000_000_000, 5_000_000_001)])
        calls = []
        def clock():
            calls.append(1)
            return next(readings)
        rows = self.csv_rows(sensors.SensorSampler(self.root, clock=clock), samples=2)
        self.assertEqual(len(calls), 2)
        self.assertEqual([row[:2] for row in rows[1:]],
                         [["2027-01-15T08:00:00.123+00:00", "1"], ["2027-01-15T08:00:05.123+00:00", "5000000001"]])
        self.assertEqual(sensors.utc_text(0), "1970-01-01T00:00:00.000+00:00")

    def test_bounds_and_early_stop(self):
        sampler = sensors.SensorSampler(self.root, clock=fixed_clock)
        for interval, samples in ((.1, 1), (61, 1), (1, 0), (1, sensors.MAX_SAMPLES + 1), (1, 1.0)):
            with self.subTest(interval=interval, samples=samples), self.assertRaises(ValueError):
                sensors.run(sampler, io.StringIO(), interval=interval, samples=samples, sleep=lambda _: None)
        stop = threading.Event()
        stop.set()
        output = io.StringIO()
        self.assertEqual(sensors.run(sampler, output, interval=.5, samples=10, stop=stop), 0)
        self.assertEqual(output.getvalue().splitlines(), [",".join(MINIMAL_COLUMNS)])


class SampleRunContextCommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "tree"
        frame_like_tree(self.root)

    def run_script(self, *arguments):
        return subprocess.run([sys.executable, str(SCRIPT), "--root", str(self.root), *arguments],
                              capture_output=True, text=True, timeout=20)

    def test_writes_csv_to_stdout_and_summary_to_stderr(self):
        result = self.run_script("--interval", "0.5", "--samples", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = list(csv.reader(io.StringIO(result.stdout)))
        self.assertEqual(rows[0], EXPECTED_COLUMNS)
        self.assertEqual(len(rows), 3)
        for row in rows[1:]:
            self.assertRegex(row[0], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}\+00:00$")
            self.assertRegex(row[1], r"^\d+$")
        summary = json.loads(result.stderr.splitlines()[-1])
        self.assertEqual((summary["status"], summary["rows"], summary["exit_code"]), ("complete", 2, 0))
        self.assertIn("unconfirmed on SteamOS 0.5.6", summary["scope"])

    def test_output_is_a_new_file_and_bounds_are_enforced(self):
        target = Path(self.temporary.name) / "context.csv"
        result = self.run_script("--interval", "0.5", "--samples", "1", "--output", str(target))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_text().splitlines()[0], ",".join(EXPECTED_COLUMNS))
        self.assertEqual(os.stat(target).st_mode & 0o777, 0o600)
        again = self.run_script("--interval", "0.5", "--samples", "1", "--output", str(target))
        self.assertEqual(again.returncode, 1)
        self.assertEqual(json.loads(again.stderr)["status"], "output_refused")
        for arguments in (("--interval", "0.1"), ("--samples", "0"), ("--samples", str(sensors.MAX_SAMPLES + 1))):
            with self.subTest(arguments=arguments):
                result = self.run_script(*arguments)
                self.assertEqual(result.returncode, 64)
                self.assertEqual(result.stdout, "")

    def test_sampler_is_not_a_runner_task_and_touches_no_vr(self):
        tasks = (ROOT / "framejacker/agent/tasks.py").read_text()
        self.assertNotIn("sample_run_context", tasks)
        self.assertNotIn("sensors", tasks)
        allowed = {"argparse", "csv", "datetime", "glob", "json", "os", "pathlib", "re", "sys", "time",
                   "framejacker.runtime", "framejacker.runtime.guard"}
        for path in (ROOT / "framejacker/runtime/sensors.py", SCRIPT):
            tree = ast.parse(path.read_text())
            imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
            imported |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
            self.assertLessEqual(imported, allowed, path.name)  # no subprocess, socket or OpenVR binding
            literals = [node.value for node in ast.walk(tree)
                        if isinstance(node, ast.Constant) and isinstance(node.value, str)]
            self.assertFalse([text for text in literals if "/dev/" in text or "steamvr.service" in text])

    def test_help_names_the_scope(self):
        with contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit):
            sys.path.insert(0, str(ROOT / "scripts"))
            try:
                import sample_run_context
            finally:
                sys.path.pop(0)
            sample_run_context.main(["--help"])
        self.assertIn("SteamOS 0.5.6", output.getvalue())


if __name__ == "__main__":
    unittest.main()
