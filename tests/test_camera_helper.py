"""The camera helper's containment of the Arcturus camera SDK (lane G, step 1).

Cloud checks on the build host, never device results. They pin: the SDK
headers are included by one translation unit only; nothing but the build
script names the Client Library; no helper source can map, read, copy or
write a pixel, set a camera control or request recording; the fake-backend
binary carries no SDK symbol; the fake backend follows the SDK's session
rules; the build script refuses every library path it must refuse and never
downloads anything; and the helper's lifecycle follows the overlay bridge's
house pattern (JSON lines, one report, exit codes 0/1/3/64).
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from sightcircuit.runtime.manifest import NATIVE_SOURCES

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
HELPER_SOURCES = sorted(TOOLS.glob("camera_helper*"))
SDK_UNIT = TOOLS / "camera_helper_sdk.cpp"
FAKE_UNIT = TOOLS / "camera_helper_fake.cpp"
BUILD_SCRIPT = ROOT / "scripts/build_camera_helper.sh"
FAKE_BINARY = ROOT / "artifacts/camera-helper/sightcircuit-camera-helper-fake"
SDK_HEADERS = ROOT / "third_party/arcturus-camera-sdk"
FIXTURE = ROOT / "tests/fixtures/camera_helper_fake_fixture.cpp"
CLIENT_LIBRARY = "libAV1CameraClient"
CXX = ["g++", "-std=c++17", "-Wall", "-Wextra", "-Wpedantic", "-Werror"]
# Tokens no helper source may contain, after comments are removed: pixel paths
# (a descriptor, a mapping, a copy, a file write, an image encoder), camera
# control, the private wire protocol and the other native tracks.
BANNED = ("cameraFd", "mmap", "ofstream", "fopen", "fwrite", "O_CREAT", "O_WRONLY", "O_RDWR", "memcpy", "imwrite",
          "stbi_", "png", "jpeg", "setControls", "ControlsRequest", "socket(", "MessageHeader", "kDefaultSocketPath",
          "av1_external_camera_provider", "capture_", "shm_common", "handcam", "openvr.h", "vulkan")
# The scripted scenarios the fake-backend fixture runs (tests/fixtures/camera_helper_fake_fixture.cpp).
SCENARIOS = {
    "success": "connect ok fps=72\nacquire ok width=2464 height=2464 repeat=2\n",
    "permission-denied": "connect server permission_denied\nconnect ok\nreader server permission_denied\n",
    "already-connected": "connect server client_already_connected\n",
    "protocol-error": "# the first acquire succeeds, its release breaks the protocol\nacquire ok\nrelease protocol\n"
                      "acquire transport errno=104\nacquire server timeout\nacquire timeout\n",
    "json": "",
}
CLAIMS = {"camera_connected": False, "reader_enabled": False, "recording_requested": False, "controls_set": False,
          "led_touched": False, "frame_sets_acquired": 0, "pixels_accessed": False, "frame_data_written": False}


def code(path):
    """A source without its line comments (the comments name what the code must not contain)."""
    return re.sub(r"//[^\n]*", "", path.read_text())


def repository_sources():
    """Every tracked source and script outside the vendored and documentation trees."""
    tracked = subprocess.run(["git", "ls-files", "-z", "tools", "sightcircuit", "scripts", "tests"], cwd=ROOT, check=True,
                             capture_output=True, timeout=30).stdout.decode().split("\0")
    return [ROOT / path for path in tracked if path]


class ContainmentSourceTests(unittest.TestCase):
    def test_the_helper_has_its_five_units_and_none_is_pinned_by_the_manifest(self):
        self.assertEqual([path.name for path in HELPER_SOURCES],
                         ["camera_helper.cpp", "camera_helper.hpp", "camera_helper_fake.cpp", "camera_helper_reporting.cpp",
                          "camera_helper_sdk.cpp"])
        for path in HELPER_SOURCES:  # steps 1–3 of the migration plan touch no pinned path
            self.assertNotIn(path.relative_to(ROOT).as_posix(), NATIVE_SOURCES)

    def test_only_the_sdk_unit_includes_the_sdk_headers(self):
        for path in repository_sources():
            if path == SDK_UNIT or path.name in ("test_arcturus_sdk_headers.py", Path(__file__).name):
                continue
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertNotIn("AV1Camera", path.read_text(errors="replace"))
        text = SDK_UNIT.read_text()
        self.assertEqual(text.count('#include "AV1CameraClient.h"'), 1)
        self.assertNotIn("AV1CameraIPCProtocol", code(SDK_UNIT))  # the protocol header comes through the client header only
        self.assertNotIn("av1_ipc", code(TOOLS / "camera_helper.hpp"))

    def test_only_the_build_script_names_the_client_library(self):
        allowed = {BUILD_SCRIPT, ROOT / "tests/test_arcturus_sdk_headers.py", ROOT / "tests" / Path(__file__).name}
        for path in repository_sources():
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertEqual(CLIENT_LIBRARY in path.read_text(errors="replace"), path in allowed)
        self.assertIn(f"{CLIENT_LIBRARY}.a", BUILD_SCRIPT.read_text())

    def test_no_helper_source_has_a_pixel_control_or_wire_protocol_path(self):
        for path in HELPER_SOURCES:
            text = code(path)
            for token in BANNED:
                with self.subTest(path=path.name, token=token):
                    self.assertNotIn(token, text)
        # The reader is enabled with recording false, nowhere else and never otherwise.
        sdk = code(SDK_UNIT)
        self.assertEqual(re.findall(r"setReaderState\([^)]*\)", sdk), ["setReaderState(active, false)"])
        for path in HELPER_SOURCES:
            if path != SDK_UNIT:
                self.assertNotIn("setReaderState", code(path))
        # The interface exposes no descriptor, no control setter and no recording flag.
        interface = code(TOOLS / "camera_helper.hpp").split("class Backend")[1].split("};")[0]
        for token in ("recording", "fd", "control(", "set_control", "led", "buffer", "pixel"):
            self.assertNotIn(token, interface.lower(), token)
        self.assertIn("set_reader_active(bool active)", interface)

    def test_the_sdk_unit_pins_the_mirrored_vocabulary(self):
        sdk = SDK_UNIT.read_text()
        for name in ("Result_PermissionDenied", "Result_ClientAlreadyConnected", "PixelFormat_NV12_UBWC",
                     "SdkError::Protocol", "kProtocolVersion == 1", "kMaxCameras", "kMaxPlanes"):
            self.assertIn(name, sdk)
        self.assertGreaterEqual(sdk.count("static_assert("), 6)

    def test_the_build_script_never_downloads(self):
        text = BUILD_SCRIPT.read_text()
        for token in ("curl", "wget", "git clone", "git fetch", "pip ", "http://", "https://"):
            self.assertNotIn(token, text)
        self.assertIn("-fsyntax-only", text)
        self.assertIn("outside the work tree", text)

    def test_the_client_library_is_ignored_at_every_depth(self):
        for path in (f"{CLIENT_LIBRARY}.a", f"artifacts/camera-helper/{CLIENT_LIBRARY}.a", f".cache/sdk/lib/{CLIENT_LIBRARY}.a"):
            result = subprocess.run(["git", "check-ignore", "-q", "--no-index", "--", path], cwd=ROOT, capture_output=True,
                                    timeout=30)
            self.assertEqual(result.returncode, 0, path)


@unittest.skipUnless(FAKE_BINARY.is_file(), "Build scripts/build_camera_helper.sh first")
class FakeBinaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def run_helper(self, *arguments, **kwargs):
        result = subprocess.run([str(FAKE_BINARY), *arguments], capture_output=True, timeout=10, **kwargs)
        lines = [json.loads(line) for line in result.stdout.decode().splitlines()] if arguments != ("--help",) else []
        for line in lines:
            self.assertEqual((line["report_kind"], line["schema_version"], line["backend"]),
                             ("sightcircuit_camera_helper", 1, "fake"))
        return result, lines

    def script(self, text):
        path = Path(self.temporary.name) / "script.txt"
        path.write_text(text)
        return str(path)

    def assert_claims(self, report):
        for field, value in CLAIMS.items():
            self.assertEqual(report[field], value, field)
        self.assertTrue(report["validation_only"])
        self.assertTrue(report["cleanup_ok"])

    def test_no_arguments_is_not_run(self):
        result, lines = self.run_helper()
        self.assertEqual((result.returncode, len(lines)), (3, 1))
        self.assertEqual((lines[0]["event"], lines[0]["status"], lines[0]["exit_code"], lines[0]["error"]),
                         ("not_run", "not_run", 3, "choose_a_mode"))
        self.assert_claims(lines[0])
        self.assertEqual(result.stderr, b"")

    def test_help_names_the_modes_and_the_absent_ones(self):
        result, _ = self.run_helper("--help")
        self.assertEqual(result.returncode, 0)
        usage = result.stdout.decode()
        self.assertIn("--validate [--fake-script FILE]", usage)
        self.assertIn("initialises nothing", usage)
        for absent in ("--describe", "--hello", "--probe-frames"):
            self.assertIn(absent, usage)
        self.assertEqual(result.stderr, b"")

    def test_validate_initialises_nothing_and_reports_once(self):
        result, lines = self.run_helper("--validate")
        self.assertEqual((result.returncode, len(lines)), (0, 1))
        report = lines[0]
        self.assertEqual((report["event"], report["status"], report["mode"], report["exit_code"]),
                         ("closed", "validated", "--validate", 0))
        self.assert_claims(report)
        self.assertIsNone(report["validated"]["fake_script"])
        self.assertEqual(report["validated"]["fake_script_steps"], 0)
        self.assertEqual(result.stderr, b"")

    def test_validate_parses_a_script_and_counts_its_steps(self):
        path = self.script("# comment\nconnect ok fps=72\n\nacquire ok width=2464 repeat=3\nacquire server timeout\nrelease ok\n")
        result, lines = self.run_helper("--validate", "--fake-script", path)
        self.assertEqual(result.returncode, 0, lines)
        validated = lines[-1]["validated"]
        self.assertEqual(validated["fake_script"], path)
        self.assertEqual(validated["fake_script_steps"], 4)
        self.assertEqual(validated["fake_script_steps_by_call"],
                         {"connect": 1, "control_state": 0, "reader": 0, "acquire": 2, "release": 1})

    def test_validate_refuses_a_bad_script_naming_the_line(self):
        for text, error in (("connect ok\nacquire maybe\n", "fake_script_line_2:unknown_outcome:maybe"),
                            ("connect server\n", "fake_script_line_1:server_outcome_needs_a_result_name"),
                            ("connect server nope\n", "fake_script_line_1:server_outcome_needs_a_result_name"),
                            ("teleport ok\n", "fake_script_line_1:unknown_call:teleport"),
                            ("acquire ok width\n", "fake_script_line_1:malformed_key_value:width"),
                            ("acquire ok width=1 width=2\n", "fake_script_line_1:repeated_key:width"),
                            ("acquire ok repeat=0\n", "fake_script_line_1:repeat_needs_a_count_or_forever"),
                            ("acquire ok errno=1\n", "fake_script_line_1:errno_needs_transport_and_a_number")):
            with self.subTest(text=text):
                result, lines = self.run_helper("--validate", "--fake-script", self.script(text))
                self.assertEqual((result.returncode, lines[-1]["status"], lines[-1]["error"]), (1, "error", error))
                self.assert_claims(lines[-1])
        result, lines = self.run_helper("--validate", "--fake-script", str(Path(self.temporary.name) / "absent.txt"))
        self.assertEqual((result.returncode, lines[-1]["error"]), (1, "fake_script_unreadable"))

    def test_invalid_arguments_exit_64_without_running(self):
        path = self.script("connect ok\n")
        for arguments in (["--unknown"], ["--describe"], ["--hello"], ["--probe-frames", "3"], ["--help", "--validate"],
                          ["--validate", "--fake-script"], ["--validate", "--fake-script", ""],
                          ["--validate", "--fake-script", path, "--fake-script", path], ["--validate", "extra"]):
            with self.subTest(arguments=arguments):
                result, lines = self.run_helper(*arguments)
                self.assertEqual((result.returncode, len(lines), lines[0]["exit_code"], lines[0]["status"]), (64, 1, 64, "error"))
                self.assert_claims(lines[0])

    def test_reports_reach_the_protocol_with_stderr_closed(self):
        result = subprocess.run([str(FAKE_BINARY), "--validate"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                preexec_fn=lambda: os.close(2), timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["status"], "validated")

    @unittest.skipUnless(shutil.which("nm"), "nm required to read the binary's symbols")
    def test_the_fake_binary_has_no_sdk_symbol(self):
        symbols = subprocess.run(["nm", "-C", str(FAKE_BINARY)], check=True, capture_output=True, text=True, timeout=30).stdout
        for token in ("AV1CameraClient", "av1_ipc", "setControls", "cameraFd", "mmap"):
            self.assertNotIn(token, symbols)
        self.assertIn("sightcircuit_camera::", symbols)


@unittest.skipUnless(shutil.which("g++"), "C++ compiler required to check the SDK unit")
class SdkUnitTests(unittest.TestCase):
    def test_the_sdk_unit_compiles_against_the_vendored_headers_alone(self):
        subprocess.run([*CXX, f"-I{SDK_HEADERS}", "-fsyntax-only", str(SDK_UNIT)], check=True, capture_output=True, timeout=60)

    @unittest.skipUnless(shutil.which("nm"), "nm required to read the object's symbols")
    def test_the_sdk_unit_needs_only_the_allowed_client_calls(self):
        # Compiled to an object, never linked: the symbols it leaves undefined are exactly the client calls it makes.
        with tempfile.TemporaryDirectory() as temporary:
            unit = Path(temporary) / "camera_helper_sdk.o"
            subprocess.run([*CXX, f"-I{SDK_HEADERS}", "-c", str(SDK_UNIT), "-o", str(unit)], check=True, capture_output=True,
                           timeout=60)
            undefined = subprocess.run(["nm", "-C", "-u", str(unit)], check=True, capture_output=True, text=True, timeout=30).stdout
        client = sorted({line.split("AV1CameraClient::", 1)[1].split("(")[0] for line in undefined.splitlines()
                         if "AV1CameraClient::" in line})
        self.assertEqual(client, ["FrameSet::release", "FrameSet::~FrameSet", "acquireFrameSet", "connect", "disconnect",
                                  "getControlState", "isConnected", "setReaderState", "~AV1CameraClient"])
        self.assertNotIn("setControls", undefined)


@unittest.skipUnless(shutil.which("g++"), "C++ compiler required for the fake-backend fixture")
class FakeBackendFixtureTests(unittest.TestCase):
    """The fake backend driven through the Backend interface, the SDK's session rules included."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temporary.name) / "camera-helper-fake-fixture"
        subprocess.run([*CXX, str(FIXTURE), str(FAKE_UNIT), str(TOOLS / "camera_helper_reporting.cpp"), "-o", str(cls.binary)],
                       check=True, capture_output=True, timeout=120)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_each_scenario_passes(self):
        for scenario, text in SCENARIOS.items():
            with self.subTest(scenario=scenario):
                script = Path(self.temporary.name) / f"{scenario}.txt"
                script.write_text(text)
                result = subprocess.run([str(self.binary), scenario, str(script)], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, f"{scenario} checks passed\n")


class BuildScriptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.out = Path(self.temporary.name) / "out"

    def run_build(self, *arguments, env=None):
        return subprocess.run(["bash", str(BUILD_SCRIPT), "--output", str(self.out), *arguments], cwd=ROOT, capture_output=True,
                              text=True, timeout=120, env=dict(os.environ, **(env or {})))

    def test_refused_library_paths_build_nothing(self):
        archive = Path(self.temporary.name) / "notlib.a"
        archive.write_text("not an archive")
        for arguments, env, message in (
                (["--client-library", "tools/camera_helper.hpp"], {}, "outside the work tree"),
                (["--client-library", str(ROOT / "artifacts/x/libAV1CameraClient.a")], {}, "not an existing file"),
                (["--client-library", str(Path(self.temporary.name) / "absent.a")], {}, "not an existing file"),
                (["--client-library", str(archive)], {}, "not an ar archive"),
                (["--client-library"], {}, "needs a path"),
                (["--client-library", str(archive)], {"SIGHTCIRCUIT_AV1_CLIENT_LIBRARY": str(archive)}, "named twice"),
                (["--bogus"], {}, "unknown argument"),
                ([], {"SIGHTCIRCUIT_AV1_CLIENT_LIBRARY": "tools/camera_helper.hpp"}, "outside the work tree")):
            with self.subTest(arguments=arguments, env=env):
                result = self.run_build(*arguments, env=env)
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn("Refused: ", result.stderr)
                self.assertIn(message, result.stderr)
                self.assertFalse(self.out.exists(), "a refused argument must build nothing")

    def test_help_exits_zero(self):
        result = self.run_build("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("never downloaded", result.stdout)

    @unittest.skipUnless(shutil.which("g++") and shutil.which("ar") and shutil.which("readelf"), "toolchain required")
    def test_an_archive_of_this_machine_reaches_the_link_which_needs_the_real_library(self):
        # A stub archive outside the work tree passes the path, format and architecture checks; the link then
        # fails for lack of the client's symbols, so no SDK binary and no report exist. Only the real Client
        # Library, supplied by the operator, completes this step; CI never has it.
        stub = Path(self.temporary.name) / "stub.cpp"
        stub.write_text("int sightcircuit_stub() { return 1; }\n")
        subprocess.run(["g++", "-c", str(stub), "-o", str(stub.with_suffix(".o"))], check=True, capture_output=True, timeout=60)
        archive = Path(self.temporary.name) / "stub.a"
        subprocess.run(["ar", "rcs", str(archive), str(stub.with_suffix(".o"))], check=True, capture_output=True, timeout=30)
        result = self.run_build("--client-library", str(archive))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Linking sightcircuit-camera-helper-sdk against", result.stdout)
        self.assertIn("undefined reference to `AV1CameraClient::connect", result.stderr)
        self.assertNotIn("setControls", result.stderr)
        self.assertTrue((self.out / "sightcircuit-camera-helper-fake").is_file())
        self.assertFalse((self.out / "sightcircuit-camera-helper-sdk").exists())
        self.assertFalse((self.out / "build-report.json").exists())
        self.assertEqual(sorted(path.name for path in self.out.iterdir()), ["sightcircuit-camera-helper-fake"])


if __name__ == "__main__":
    unittest.main()
