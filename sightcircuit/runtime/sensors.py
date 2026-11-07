"""Run-context sensor sampler: one CSV row of headset load, heat and power per interval.

An optional sidecar beside a latency run, so a run's numbers can be read
against CPU load, thermal zones, throttle state, clock caps, GPU frequency,
power rails, battery and available memory. It only reads ``/proc`` and
``/sys``: no OpenVR, no camera, no SteamVR process, no write outside its own
output. It is never a Tier A runner task (``sightcircuit/agent/tasks.py``).
``scripts/sample_run_context.py`` is the command.

The Frame paths are **unconfirmed on SteamOS 0.5.6**: upstream tested them on
SteamOS 0.4.5. A read-only sysfs listing on Frame is a later device step that
needs the owner's approval. Until then, a missing reading is a blank cell, never
a sentinel, and an absent sensor family yields no column.

Ported from JonahSimon/steam-frame-harness ``frame/frame-sensors.py`` at commit
6c16bf39780acbda28dfac41269b8fd82d97b27d, whose sensor paths come from
sasaken1102r/frame-perf-overlay ``headset/sensors.cpp`` at commit
066a5c87b4cc57a54ce60fbaea69e9fa39ae3a94 (both MIT; see THIRD_PARTY.md).
Changes: missing readings are blank rather than -999 (and a missing throttle
state is blank rather than 0), the Wi-Fi columns and their ``iw`` subprocess are
dropped, the local ``time`` column becomes one clock reading per row written as
UTC with offset and as monotonic nanoseconds, paths resolve under a ``root`` so
tests can use a fake tree, and output is bounded.

steam-frame-harness notice:
    MIT License, Copyright (c) 2026 Jonah Paul Simon

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

frame-perf-overlay notice: MIT License, Copyright (c) 2026 sasaken1102r and
contributors, with the same permission text.
"""
import csv
import datetime
import glob
import os
import re
import time

UNCONFIRMED_SCOPE = ("Frame sysfs paths unconfirmed on SteamOS 0.5.6 (upstream tested 0.4.5); "
                     "run context, not latency or device evidence")
INTERVAL_LIMITS = (0.5, 60.0)
MAX_SAMPLES = 7200
VADC_NAME = "c400000.spmi:pmic@0:vadc@9000"
VADC_CHANNELS = (("skin_display", "display_left_therm"), ("skin_exhaust", "pcba_at_fan_exhaust"),
                 ("skin_heatsink", "pcba_at_heat_sink_fins"))
RAIL_HWMON_PREFIX = "max34417"


def _read(path):
    try:
        with open(path) as handle:
            return handle.read().strip()
    except (OSError, UnicodeDecodeError):
        return ""


def _number(path, scale=1.0):
    """The file's number divided by ``scale``; None when the file or its number is missing."""
    if not path:
        return None
    try:
        return float(_read(path)) / scale
    except ValueError:
        return None


def _maximum(values):
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _policy_index(path):
    digits = re.sub(r"\D", "", os.path.basename(path))
    return int(digits) if digits else -1


def utc_text(wall_ns):
    """ISO 8601 UTC with an explicit +00:00 offset, to the millisecond."""
    moment = datetime.datetime.fromtimestamp(wall_ns / 1e9, datetime.timezone.utc)
    return moment.isoformat(timespec="milliseconds")


def _clock():
    # One reading per row: both clocks are read here, together, once.
    return time.time_ns(), time.monotonic_ns()


class SensorSampler:
    """Discovers the sensors under ``root`` once, then samples one row per call."""

    def __init__(self, root="/", clock=_clock):
        self.root = os.fspath(root)
        self.clock = clock
        def under(*parts):
            return os.path.join(self.root, *parts)
        def matches(*parts):
            return sorted(glob.glob(under(*parts)))
        zones = {_read(zone + "/type"): zone + "/temp" for zone in matches("sys/class/thermal/thermal_zone*")}
        self.cpu_zones = [p for kind, p in zones.items() if kind.startswith("cpu") and not kind.startswith("cpuss")]
        self.gpu_zones = [p for kind, p in zones.items() if kind.startswith("gpuss")]
        self.ddr_zone = next((p for kind, p in zones.items() if kind.startswith("ddr")), "")
        cooling = {_read(c + "/type"): c + "/cur_state" for c in matches("sys/class/thermal/cooling_device*")}
        self.cpu_cooling = [p for kind, p in cooling.items() if kind.startswith("cpufreq-cpu")]
        self.gpu_cooling = [p for kind, p in cooling.items() if kind.startswith("devfreq-") and "gpu" in kind]
        rails = {}  # label -> power*_input in microwatts
        for hwmon in matches("sys/class/hwmon/hwmon*"):
            if _read(hwmon + "/name").startswith(RAIL_HWMON_PREFIX):
                for path in sorted(glob.glob(hwmon + "/power*_input")):
                    label = _read(path.replace("_input", "_label")) or os.path.basename(path)
                    if label != "unused":
                        rails[label] = path
        self.rails = {label: rails[label] for label in sorted(rails, key=lambda label: (label != "vph", label))}
        self.policies = sorted(matches("sys/devices/system/cpu/cpufreq/policy*"), key=_policy_index)
        self.gpu_devfreq = next(iter(matches("sys/class/devfreq/*gpu*")), "")
        supplies = matches("sys/class/power_supply/*")
        self.battery = next((p for p in supplies if _read(p + "/type") == "Battery"), "")
        self.charger = next((p for p in supplies if "charger" in os.path.basename(p)), "")
        self.vadc = next((d for d in matches("sys/bus/iio/devices/iio:device*") if _read(d + "/name") == VADC_NAME), "")
        self.proc_stat = under("proc/stat")
        self.meminfo = under("proc/meminfo")
        names = [os.path.basename(p) for p in self.policies]
        self.columns = (["utc", "monotonic_ns", "cpu_util_pct", "cpu_temp_max", "gpu_temp_max", "ddr_temp"]
                        + [column for column, _ in VADC_CHANNELS]
                        + ["bat_temp", "throttle_cpu", "throttle_gpu"]
                        + [f"{name}_mhz" for name in names] + [f"{name}_cap_mhz" for name in names]
                        + ["gpu_mhz"] + [f"w_{label}" for label in self.rails]
                        + ["bat_pct", "bat_status", "bat_w", "charger_online", "mem_avail_mb"])
        self.previous_ticks = self._ticks()

    def _ticks(self):
        """(total, idle) jiffies from the first line of /proc/stat, or None."""
        fields = _read(self.proc_stat).split("\n")[0].split()
        if len(fields) < 6 or fields[0] != "cpu":
            return None
        try:
            values = [int(value) for value in fields[1:]]
        except ValueError:
            return None
        return sum(values), values[3] + values[4]

    def _cpu_util(self):
        current = self._ticks()
        previous, self.previous_ticks = self.previous_ticks, current
        if current is None or previous is None:
            return None
        total = current[0] - previous[0]
        return 100 * (1 - (current[1] - previous[1]) / total) if total > 0 else None

    def _throttle(self, paths):
        states = [_number(path) for path in paths]
        present = [int(state) for state in states if state is not None]
        return max(present) if present else None

    def sample(self):
        """One row as a dict keyed by ``columns``; a missing reading is None."""
        wall_ns, monotonic_ns = self.clock()
        voltage = _number(self.battery and self.battery + "/voltage_now", 1e6)
        current = _number(self.battery and self.battery + "/current_now", 1e6)
        memory = re.search(r"MemAvailable:\s+(\d+)", _read(self.meminfo))
        row = {
            "utc": utc_text(wall_ns), "monotonic_ns": monotonic_ns, "cpu_util_pct": self._cpu_util(),
            "cpu_temp_max": _maximum(_number(p, 1000) for p in self.cpu_zones),
            "gpu_temp_max": _maximum(_number(p, 1000) for p in self.gpu_zones),
            "ddr_temp": _number(self.ddr_zone, 1000),
        }
        for column, channel in VADC_CHANNELS:
            row[column] = _number(self.vadc and f"{self.vadc}/in_temp_{channel}_input", 1000)
        row.update(bat_temp=_number(self.battery and self.battery + "/temp", 10),
                   throttle_cpu=self._throttle(self.cpu_cooling), throttle_gpu=self._throttle(self.gpu_cooling))
        for policy in self.policies:
            row[f"{os.path.basename(policy)}_mhz"] = _number(policy + "/scaling_cur_freq", 1000)
        for policy in self.policies:
            row[f"{os.path.basename(policy)}_cap_mhz"] = _number(policy + "/scaling_max_freq", 1000)
        row["gpu_mhz"] = _number(self.gpu_devfreq and self.gpu_devfreq + "/cur_freq", 1e6)
        for label, path in self.rails.items():
            row[f"w_{label}"] = _number(path, 1e6)
        row.update(bat_pct=_number(self.battery and self.battery + "/capacity"),
                   bat_status=(_read(self.battery + "/status") or None) if self.battery else None,
                   bat_w=voltage * current if voltage is not None and current is not None else None,
                   charger_online=(_read(self.charger + "/online") or None) if self.charger else None,
                   mem_avail_mb=int(memory.group(1)) // 1024 if memory else None)
        return row


def cell(value):
    """CSV text: blank for a missing reading, two decimals for a float."""
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def run(sampler, output, *, interval, samples, stop=None, sleep=None):
    """Write the header, then up to ``samples`` rows ``interval`` seconds apart; returns the rows written.

    ``stop`` (an event) ends the run early between rows. Each row is flushed.
    """
    if not INTERVAL_LIMITS[0] <= interval <= INTERVAL_LIMITS[1]:
        raise ValueError("interval_out_of_range")
    if type(samples) is not int or not 1 <= samples <= MAX_SAMPLES:
        raise ValueError("samples_out_of_range")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(sampler.columns)
    output.flush()
    written = 0
    while written < samples:
        if stop is not None:
            if stop.wait(interval):
                break
        else:
            (sleep or time.sleep)(interval)
        row = sampler.sample()
        writer.writerow([cell(row[column]) for column in sampler.columns])
        output.flush()
        written += 1
    return written
