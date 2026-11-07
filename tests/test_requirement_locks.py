"""Each hashed lock agrees with its requirement file (P1, 2026-11-07).

requirements-{replay,window,dev}.lock are generated from the requirement files
by scripts/compile_locks.sh and are what the setup scripts install. For each
tested interpreter version the lock must pin exactly the packages and versions
the requirement file names, every entry must carry hashes, and the header must
name the regeneration command. Standard library only: the dependency-free job
runs this module.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SETS = ("replay", "window", "dev")
TESTED_VERSIONS = ("3.12", "3.14")  # pyproject.toml requires-python
REGENERATE = "bash scripts/compile_locks.sh"
_REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9.]+)\s*(?:;\s*(.*))?$")
_MARKER = re.compile(r"""^(python_version|python_full_version)\s*(<=|>=|==|<|>)\s*['"]([0-9.]+)['"]$""")


def _version_tuple(text):
    return tuple(int(part) for part in text.split("."))


def marker_holds(marker, python_version):
    """Evaluate the one marker shape the requirement files use; anything else is an error."""
    if marker is None:
        return True
    match = _MARKER.fullmatch(marker.strip())
    if not match:
        raise ValueError(f"unsupported marker: {marker!r}")
    variable, operator, value = match.groups()
    actual = _version_tuple(python_version if variable == "python_version" else python_version + ".0")
    wanted = _version_tuple(value)
    if variable == "python_version":
        wanted = wanted[:2]
    return {"<": actual < wanted, "<=": actual <= wanted, "==": actual == wanted,
            ">": actual > wanted, ">=": actual >= wanted}[operator]


def requirement_pins(path, seen=None):
    """(name, version, marker) for every pin, following -r includes."""
    seen = seen or set()
    pins = []
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-r "):
            included = path.parent / line[3:].strip()
            if included not in seen:
                seen.add(included)
                pins.extend(requirement_pins(included, seen))
            continue
        match = _REQUIREMENT.fullmatch(line)
        if not match:
            raise ValueError(f"{path.name}: not an exact pin: {raw!r}")
        pins.append((match.group(1).lower(), match.group(2), match.group(3)))
    return pins


def lock_entries(path):
    """(name, version, marker, hash count) for every entry of a requirements-format lock."""
    entries = []
    current = None
    for raw in path.read_text().splitlines():
        line = raw.rstrip("\\").strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("--hash=sha256:"):
            if current is None:
                raise ValueError(f"{path.name}: hash before any requirement: {raw!r}")
            current[3] += 1
            continue
        match = _REQUIREMENT.fullmatch(line)
        if not match:
            raise ValueError(f"{path.name}: unexpected line: {raw!r}")
        current = [match.group(1).lower(), match.group(2), match.group(3), 0]
        entries.append(current)
    return [tuple(entry) for entry in entries]


def resolved(pins, python_version):
    return {(name, version) for name, version, marker, *_ in pins if marker_holds(marker, python_version)}


class RequirementLocks(unittest.TestCase):
    def test_every_set_has_a_lock_naming_its_regeneration_command(self):
        for name in SETS:
            with self.subTest(set=name):
                lock = ROOT / f"requirements-{name}.lock"
                self.assertTrue(lock.is_file(), f"{lock.name} is missing; run {REGENERATE}")
                header = lock.read_text().splitlines()[:2]
                self.assertTrue(any(REGENERATE in line and line.startswith("#") for line in header), header)

    def test_each_lock_pins_exactly_its_requirement_file_for_each_tested_version(self):
        for name in SETS:
            pins = requirement_pins(ROOT / f"requirements-{name}.txt")
            entries = lock_entries(ROOT / f"requirements-{name}.lock")
            for python_version in TESTED_VERSIONS:
                with self.subTest(set=name, python=python_version):
                    expected = resolved(pins, python_version)
                    locked = resolved(entries, python_version)
                    self.assertEqual(locked, expected,
                                     f"requirements-{name}.lock does not match requirements-{name}.txt; run {REGENERATE}")
                    self.assertEqual(len({pin for pin, _ in expected}), len(expected), "a package pinned twice for one version")

    def test_every_lock_entry_carries_hashes(self):
        for name in SETS:
            for package, version, _marker, hashes in lock_entries(ROOT / f"requirements-{name}.lock"):
                with self.subTest(set=name, package=package, version=version):
                    self.assertGreater(hashes, 0, f"{package}=={version} has no --hash entries")

    def test_the_dev_set_carries_the_replay_decoder(self):
        self.assertIn(("av", "16.1.0", None), requirement_pins(ROOT / "requirements-dev.txt"))

    def test_the_setup_scripts_install_from_the_locks(self):
        for script, lock in (("setup_replay.sh", "requirements-replay.lock"), ("setup_window.sh", "requirements-window.lock"),
                             ("setup.sh", "requirements-dev.lock")):
            with self.subTest(script=script):
                text = (ROOT / "scripts" / script).read_text()
                self.assertIn(lock, text)
                self.assertIn("--require-hashes", text)
                self.assertNotRegex(text, r"-r [^\n]*requirements-[a-z]+\.txt", f"{script} still installs an unhashed file")

    def test_the_marker_evaluator_matches_the_two_spellings(self):
        for marker in ('python_version < "3.14"', "python_full_version < '3.14'"):
            self.assertTrue(marker_holds(marker, "3.12"))
            self.assertFalse(marker_holds(marker, "3.14"))
        for marker in ('python_version >= "3.14"', "python_full_version >= '3.14'"):
            self.assertFalse(marker_holds(marker, "3.12"))
            self.assertTrue(marker_holds(marker, "3.14"))
        with self.assertRaises(ValueError):
            marker_holds("sys_platform == 'linux'", "3.12")


if __name__ == "__main__":
    unittest.main()
