"""Containment of the Arcturus camera SDK in the camera helper.

Which files may include the SDK headers, what the helper sources may never
contain (and which of them alone may read a buffer: the two pixel paths, and
the fake's memfd), the status mapping run against the vendored headers, API
drift of the SDK translation unit (compiled, never linked) with its symbol
allow-list, coverage of every vendored enumerator, the helper's own SHA-256,
the embedded compute shader's three variants against their source, that the
read-only ``--query-gpu`` mode reaches no backend, and the build script's
refusals against fake SDK directories made outside the checkout. None of this
needs the Client Library; no file named like it is ever created inside the
checkout.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VENDORED = ROOT / "third_party/arcturus-camera-sdk"
TOOLS = ROOT / "tools"
SCRIPT = ROOT / "scripts/build_camera_helper.sh"
SDK_HEADERS = ("AV1CameraClient.h", "AV1CameraIPCProtocol.h")
SDK_INCLUDERS = {"tools/camera_backend_sdk.cpp", "tools/camera_status_map.hpp",
                 "tests/fixtures/camera_status_map_fixture.cpp"}
METADATA_SOURCES = ("tools/camera_backend.hpp", "tools/camera_backend_sdk.cpp", "tools/camera_status_map.hpp",
                    "tools/camera_helper.cpp", "tools/camera_sha256.hpp", "tools/camera_export_protocol.hpp",
                    "tools/camera_ycbcr.hpp", "tools/camera_pixels.hpp")
PIXEL_SOURCES = ("tools/camera_pixels_cpu.cpp", "tools/camera_pixels_gpu.cpp")
HELPER_SOURCES = METADATA_SOURCES + PIXEL_SOURCES + ("tools/camera_backend_fake.cpp", "tools/camera_convert_spirv.hpp")
# Never, in any helper source: the SDK's private transport, control writes, the
# capture track's names, confirmation bypasses, OpenCV, and every way of writing a
# file (the lock in camera_helper.cpp is the one O_CREAT, listed below).
BANNED_TEXT = ("socket(", "sockaddr_un", "kDefaultSocketPath", "av1_external_camera_provider",
               "passthrough_camera.lock", "MessageHeader", "setControls", "ControlsRequest",
               "redRecordingLedEnabled =", "--yes", "--no-confirm", "fopen(", "ofstream", "O_WRONLY", "O_CREAT",
               "imwrite", "cv::", "opencv")
# Only the pixel paths read a buffer; only the SDK unit reads a descriptor from the
# SDK; only the fake creates one (a memfd) and maps it to fill it.
PIXEL_ONLY_TEXT = ("mmap", "DMA_BUF", "vkImport", "VkImportMemoryFdInfo", "dlopen", "VkDevice")
ALLOWED_TEXT = {
    "tools/camera_helper.cpp": {"O_CREAT"},
    "tools/camera_backend_sdk.cpp": {"cameraFd("},
    "tools/camera_backend_fake.cpp": {"mmap", "memfd_create"},
}
BANNED_INCLUDE = re.compile(r'#\s*include\s*[<"](?:capture_\w+\.hpp|handcam_proto\.h)')
VULKAN_INCLUDE = re.compile(r'#\s*include\s*[<"]vulkan/')
CXX11_SOURCES = ("tools/camera_backend.hpp", "tools/camera_status_map.hpp", "tools/camera_sha256.hpp",
                 "tools/camera_backend_sdk.cpp", "tools/camera_backend_fake.cpp", "tools/camera_export_protocol.hpp",
                 "tools/camera_ycbcr.hpp", "tools/camera_pixels.hpp", "tools/camera_pixels_cpu.cpp")
ALLOWED_SYMBOLS = {
    "_ZN15AV1CameraClient7connectEj", "_ZN15AV1CameraClient10disconnectEv", "_ZNK15AV1CameraClient11isConnectedEv",
    "_ZN15AV1CameraClient15getControlStateERN7av1_ipc12ControlStateE", "_ZN15AV1CameraClient14setReaderStateEbb",
    "_ZN15AV1CameraClient15acquireFrameSetERNS_8FrameSetEj", "_ZN15AV1CameraClient8FrameSet7releaseEv",
    "_ZN15AV1CameraClient8FrameSetD1Ev", "_ZN15AV1CameraClientD1Ev", "_ZNK15AV1CameraClient6Status7messageEv",
}
CXX11 = ["-std=c++11", "-fno-exceptions", "-fno-rtti", "-Wall", "-Wextra", "-Wpedantic", "-Werror"]
# Enumerators the helper reports raw (as a hex bitmask) rather than by name. A
# new bit in the header fails the coverage test until it is listed or named.
RAW_BITMASK_ENUMERATORS = {
    "ControlBits": {"Control_RedRecordingLed", "Control_SensorBitDepth", "Control_AutoWhiteBalance", "Control_Hdr",
                    "Control_ToneMapping", "Control_TransferFunction", "Control_Resolution", "Control_Fps",
                    "Control_Brightness", "Control_ColorPrimaries", "Control_YCbCrEncoding", "Control_ExposureGain"},
}
COVERED_ENUMS = ("PixelFormat", "Result", "TransferFunctionValue", "ColorPrimariesValue", "YCbCrEncodingValue",
                 "CameraFrameModel", "OfflineCalSource", "ResolutionModeValue", "AutoWhiteBalanceModeValue",
                 "ControlBits")
SOURCE_REVISION = "536217ebbc0456df5db53658409b048cdd272785"
AGREEMENT_VERSION = "2026-11-08"


def enumerators(text, name):
    """The enumerator names of ``enum [class] name``, comments stripped."""
    match = re.search(r"enum(?:\s+class)?\s+" + re.escape(name) + r"\b[^{]*\{(.*?)\}", text, re.S)
    assert match, name
    body = re.sub(r"//[^\n]*", "", match.group(1))
    return [piece.split("=")[0].strip() for piece in body.split(",") if piece.strip()]


def source_files():
    for path in ROOT.rglob("*"):
        if path.suffix not in (".cpp", ".hpp", ".h", ".cc", ".c") or not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if relative.parts[0] in ("third_party", ".cache", "artifacts", ".git") or relative.parts[0].startswith(".venv"):
            continue
        yield relative.as_posix(), path.read_text(errors="replace")


class IncludeAndTextBoundaries(unittest.TestCase):
    def test_only_the_translation_units_include_the_sdk_headers(self):
        includers = set()
        for relative, text in source_files():
            if any(re.search(r'#\s*include\s*[<"]' + re.escape(header) + '[">]', text) for header in SDK_HEADERS):
                includers.add(relative)
        # The fixture reaches the headers through camera_status_map.hpp; it may include them directly too.
        self.assertLessEqual(includers, SDK_INCLUDERS, includers - SDK_INCLUDERS)
        self.assertLessEqual({"tools/camera_backend_sdk.cpp", "tools/camera_status_map.hpp"}, includers)

    def test_helper_sources_contain_no_banned_text(self):
        for relative in HELPER_SOURCES:
            text = (ROOT / relative).read_text()
            allowed = ALLOWED_TEXT.get(relative, set())
            for banned in BANNED_TEXT:
                if banned in allowed:
                    continue
                with self.subTest(file=relative, text=banned):
                    self.assertNotIn(banned, text)
            with self.subTest(file=relative, text="banned include"):
                self.assertIsNone(BANNED_INCLUDE.search(text))
            with self.subTest(file=relative, text="environment"):
                self.assertLessEqual(set(re.findall(r'getenv\("([^"]+)"\)', text)), {"XDG_RUNTIME_DIR"})
        self.assertEqual((ROOT / "tools/camera_helper.cpp").read_text().count("O_CREAT"), 1)

    def test_only_the_pixel_paths_read_buffers(self):
        for relative in HELPER_SOURCES:
            text = (ROOT / relative).read_text()
            allowed = ALLOWED_TEXT.get(relative, set())
            if relative not in PIXEL_SOURCES:
                for banned in PIXEL_ONLY_TEXT + ("cameraFd(", "memfd_create"):
                    if banned in allowed:
                        continue
                    with self.subTest(file=relative, text=banned):
                        self.assertNotIn(banned, text)
            with self.subTest(file=relative, text="vulkan include"):
                self.assertEqual(VULKAN_INCLUDE.search(text) is not None, relative == "tools/camera_pixels_gpu.cpp")
        for relative in PIXEL_SOURCES:
            text = (ROOT / relative).read_text()
            with self.subTest(file=relative):
                self.assertNotIn("cameraFd(", text)
                self.assertNotIn("AV1", text)
                self.assertNotIn("av1_ipc", text)
        # The SDK unit hands out the descriptor and does nothing else with it.
        sdk = (ROOT / "tools/camera_backend_sdk.cpp").read_text()
        self.assertEqual(len(re.findall(r"cameraFd\(", sdk)), 1)
        for banned in ("mmap", "ioctl", "read(", "pread", "dup("):
            with self.subTest(text=banned):
                self.assertNotIn(banned, sdk)

    # Every "--flag" literal the helper parses. The only flag that stands in for the typed
    # confirmation is --parent-confirmed (the owner's decision of 2026-11-09, export mode only).
    HELPER_FLAGS = {"--help", "--describe", "--query-gpu", "--hello", "--probe-frames", "--export-frames", "--max-seconds", "--max-frames",
                    "--fps", "--cameras", "--pixel-path", "--interval-ms", "--timeout-ms", "--fake-scenario",
                    "--parent-confirmed"}

    def test_the_helper_parses_only_the_listed_flags(self):
        text = (ROOT / "tools/camera_helper.cpp").read_text()
        parsed = set(re.findall(r'argument == "(--[a-z-]+)"', text))
        self.assertEqual(parsed, self.HELPER_FLAGS)
        literals = set(re.findall(r'"(--[a-z][a-z-]*)', text))
        self.assertLessEqual(literals, self.HELPER_FLAGS, literals - self.HELPER_FLAGS)
        self.assertNotIn("getenv", text.replace('getenv("XDG_RUNTIME_DIR")', ""))
        # The flag is accepted only with --export-frames, and nothing else skips confirm().
        self.assertIn('options.error = "--parent-confirmed applies to --export-frames"', text)
        self.assertEqual(text.count("confirm(notice_for(options), reason)"), 1)
        self.assertEqual(text.count("options.parent_confirmed)"), 2)  # the parse check and the one branch in main

    def test_the_gpu_query_mode_makes_no_sdk_call_and_reads_no_buffer(self):
        text = (ROOT / "tools/camera_helper.cpp").read_text()
        # main leaves for the query before a backend exists, before the lock and before the confirmation.
        main = text[text.index("int main(int argc, char** argv)"):]
        leave = main.index("if (options.mode == Options::query_gpu) return run_query_gpu();")
        for later in ("make_camera_backend(", "Session session(", "take_lock(", "confirm(", "session.run()"):
            with self.subTest(after=later):
                self.assertEqual(main.count(later), 1)
                self.assertLess(leave, main.index(later))
        # The mode's body: one call into the GPU unit, one record, and no name of the backend, the session,
        # a descriptor or a pixel path that converts.
        body = re.search(r"\nint run_query_gpu\(\) \{\n(.*?)\n\}\n", text, re.S).group(1)
        self.assertEqual(body.count("query_gpu_convert(gpu, error)"), 1)
        self.assertEqual(body.count("emit(object)"), 1)
        for banned in ("backend", "Backend", "Session", "session", "connect", "acquire", "reader", "fd", "convert(",
                       "make_gpu_convert_path", "make_cpu_linear_path", "write_packet", "STDERR_FILENO", "note("):
            with self.subTest(text=banned):
                self.assertNotIn(banned, body.replace("query_gpu_convert(gpu, error)", ""))
        self.assertEqual(text.count("run_query_gpu()"), 2)  # its definition and the one call in main
        self.assertEqual(text.count("query_gpu_convert("), 1)
        # In the GPU unit the query asks the driver and builds pipelines; it imports nothing, maps nothing but the
        # path's own output buffer (never touched here) and binds no memory to its trial image.
        gpu = (ROOT / "tools/camera_pixels_gpu.cpp").read_text()
        query = gpu[gpu.index("// ---- the query (--query-gpu)"):gpu.index("// ---- teardown")]
        for banned in ("import_memory(", "import_frame(", "import_modifier(", "import_linear_copy(", "vkAllocateMemory",
                       "vkBindImageMemory", "vkMapMemory", "frame.fd", "dup(", "dispatch_and_read(", "vkQueueSubmit",
                       "ensure_output("):
            with self.subTest(text=banned):
                self.assertNotIn(banned, query)
        entry = gpu[gpu.index("bool query_gpu_convert("):]
        for banned in ("convert(frame", ".convert(", "plan("):
            self.assertNotIn(banned, entry)
        # The strategy is the driver's answer, never an option of the helper: only the test fixture restricts it.
        self.assertEqual(re.findall(r"make_gpu_convert_path\(([^)]*)\)", text), ["", ""])
        for banned in ("only_strategy", "plane_views_", "ycbcr_sampler"):
            self.assertNotIn(banned, text)

    def test_reader_state_is_never_recording(self):
        calls = []
        for relative in HELPER_SOURCES:
            for arguments in re.findall(r"setReaderState\(([^()]*)\)", (ROOT / relative).read_text()):
                parts = [part.strip() for part in arguments.split(",")]
                with self.subTest(file=relative, call=arguments):
                    self.assertEqual(len(parts), 2)
                    self.assertEqual(parts[1], "false")
                calls.append(relative)
        self.assertEqual(set(calls), {"tools/camera_backend_sdk.cpp"})

    def test_the_cxx11_files_do_not_include_the_cxx17_lifecycle_header(self):
        for relative in CXX11_SOURCES:
            with self.subTest(file=relative):
                self.assertNotIn("overlay_lifecycle.hpp", (ROOT / relative).read_text())
        self.assertIn('#include "overlay_lifecycle.hpp"', (ROOT / "tools/camera_helper.cpp").read_text())

    # The shader's variants, one per import strategy of the GPU path: the array's name and the define it is built with.
    SHADER_VARIANTS = (("kCameraConvert", None), ("kCameraConvertUnorm", "CAMERA_CONVERT_UNORM"),
                       ("kCameraConvertYcbcr", "CAMERA_CONVERT_YCBCR"))

    def test_the_embedded_shader_matches_its_source(self):
        header = (ROOT / "tools/camera_convert_spirv.hpp").read_text()
        source = (ROOT / "tools/camera_convert.comp").read_bytes()
        digest = re.search(r'kCameraConvertSourceSha256 = "([0-9a-f]{64})"', header).group(1)
        self.assertEqual(digest, hashlib.sha256(source).hexdigest(),
                         "camera_convert.comp changed; run scripts/build_camera_helper.sh --shader")
        for banned in ("imwrite", "fopen", "#include <vulkan"):
            self.assertNotIn(banned, header)
        # Exactly the three modules, each with its word count, its own digest and the SPIR-V magic.
        self.assertEqual(re.findall(r"const uint32_t (\w+)Spirv\[", header), [name for name, _ in self.SHADER_VARIANTS])
        self.assertEqual(len(re.findall(r"SpirvSha256 = ", header)), len(self.SHADER_VARIANTS))
        modules = {}
        for name, define in self.SHADER_VARIANTS:
            with self.subTest(module=name):
                count = int(re.search(rf"\b{name}SpirvWords = (\d+);", header).group(1))
                body = re.search(rf"const uint32_t {name}Spirv\[{count}\] = \{{(.*?)\}};", header, re.S).group(1)
                words = [int(word, 16) for word in re.findall(r"0x([0-9a-f]{8})", body)]
                self.assertEqual(len(words), count)
                self.assertEqual(words[0], 0x07230203)  # the SPIR-V magic
                binary = struct.pack(f"<{count}I", *words)
                recorded = re.search(rf'\b{name}SpirvSha256 = "([0-9a-f]{{64}})"', header).group(1)
                self.assertEqual(recorded, hashlib.sha256(binary).hexdigest())
                modules[name] = binary
                if define:
                    self.assertIn(f"defined({define})".encode(), source)
                with tempfile.TemporaryDirectory() as work:
                    module = Path(work) / "module.spv"
                    module.write_bytes(binary)
                    if shutil.which("spirv-val"):
                        result = subprocess.run(["spirv-val", "--target-env", "vulkan1.1", str(module)], capture_output=True,
                                                text=True, timeout=60)
                        self.assertEqual(result.returncode, 0, result.stderr)
                    compiler = shutil.which("glslangValidator")
                    recorded_compiler = re.search(r"// compiler: (.*)", header).group(1)
                    if compiler and subprocess.run([compiler, "--version"], capture_output=True, text=True,
                                                   timeout=60).stdout.splitlines()[0] == recorded_compiler:
                        # With the compiler the header names, the source gives these very words.
                        rebuilt = Path(work) / "rebuilt.spv"
                        subprocess.run([compiler, "--target-env", "vulkan1.1", *([f"-D{define}"] if define else []), "-V",
                                        str(ROOT / "tools/camera_convert.comp"), "-o", str(rebuilt)], check=True,
                                       capture_output=True, text=True, timeout=120)
                        self.assertEqual(rebuilt.read_bytes(), binary)
        self.assertEqual(len(set(modules.values())), len(self.SHADER_VARIANTS))  # three different modules
        # The GPU path uses each module for its strategy and nothing else carries SPIR-V.
        gpu = (ROOT / "tools/camera_pixels_gpu.cpp").read_text()
        for name, _ in self.SHADER_VARIANTS:
            self.assertEqual(gpu.count(f"shader_info.pCode = {name}Spirv;"), 1)
            self.assertEqual(gpu.count(f"{name}SpirvWords * sizeof(uint32_t)"), 1)

    def test_every_vendored_enumerator_is_mapped_or_listed_raw(self):
        headers = "".join((VENDORED / name).read_text() for name in SDK_HEADERS)
        mapped = (ROOT / "tools/camera_backend_sdk.cpp").read_text() + (ROOT / "tools/camera_status_map.hpp").read_text()
        for enum in COVERED_ENUMS:
            names = enumerators(headers, enum)
            self.assertTrue(names, enum)
            for name in names:
                with self.subTest(enum=enum, enumerator=name):
                    if enum in RAW_BITMASK_ENUMERATORS:
                        self.assertIn(name, RAW_BITMASK_ENUMERATORS[enum])
                    else:
                        self.assertRegex(mapped, r"\b" + re.escape(name) + r"\b")
        if "ControlBits" in RAW_BITMASK_ENUMERATORS:
            self.assertEqual(set(enumerators(headers, "ControlBits")), RAW_BITMASK_ENUMERATORS["ControlBits"])
        client = (VENDORED / "AV1CameraClient.h").read_text()
        status_map = (ROOT / "tools/camera_status_map.hpp").read_text()
        for name in enumerators(client, "Error"):
            with self.subTest(enum="Status::Error", enumerator=name):
                self.assertIn(f"Error::{name}", status_map)


@unittest.skipUnless(shutil.which("g++") and shutil.which("nm"), "C++ compiler and nm required")
class CompiledBoundaries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.work = Path(cls.temporary.name)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def compile(self, source, output, *extra, flags=CXX11):
        subprocess.run(["g++", *flags, f"-I{TOOLS}", *extra, str(source), "-o", str(output)], check=True,
                       capture_output=True, text=True, timeout=300)

    def test_status_mapping_holds_against_the_vendored_headers(self):
        fixture = self.work / "status-map"
        self.compile(ROOT / "tests/fixtures/camera_status_map_fixture.cpp", fixture, f"-I{VENDORED}")
        result = subprocess.run([str(fixture)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("every row holds", result.stdout)

    def test_sdk_unit_compiles_as_cxx11_and_references_only_allowed_symbols(self):
        obj = self.work / "camera_backend_sdk.o"
        self.compile(ROOT / "tools/camera_backend_sdk.cpp", obj, f"-I{VENDORED}", "-c")
        symbols = subprocess.run(["nm", "-u", "--format=just-symbols", str(obj)], check=True, capture_output=True,
                                 text=True, timeout=30).stdout.split()
        sdk_symbols = {symbol for symbol in symbols if "15AV1CameraClient" in symbol}
        self.assertTrue(sdk_symbols)
        self.assertLessEqual(sdk_symbols, ALLOWED_SYMBOLS, sdk_symbols - ALLOWED_SYMBOLS)
        for required in ("_ZN15AV1CameraClient7connectEj", "_ZN15AV1CameraClient14setReaderStateEbb",
                         "_ZN15AV1CameraClient15acquireFrameSetERNS_8FrameSetEj",
                         "_ZN15AV1CameraClient8FrameSet7releaseEv"):
            self.assertIn(required, sdk_symbols)
        self.assertFalse([symbol for symbol in symbols if "setControls" in symbol])

    def test_fake_unit_compiles_as_cxx11_without_sdk_symbols(self):
        obj = self.work / "camera_backend_fake.o"
        self.compile(ROOT / "tools/camera_backend_fake.cpp", obj, "-DSIGHTCIRCUIT_CAMERA_BACKEND_FAKE=1", "-c")
        symbols = subprocess.run(["nm", "-u", "--format=just-symbols", str(obj)], check=True, capture_output=True,
                                 text=True, timeout=30).stdout
        self.assertNotIn("15AV1CameraClient", symbols)

    def test_cpu_pixel_path_compiles_as_cxx11_without_sdk_or_vulkan_symbols(self):
        obj = self.work / "camera_pixels_cpu.o"
        self.compile(ROOT / "tools/camera_pixels_cpu.cpp", obj, "-c")
        symbols = subprocess.run(["nm", "-u", "--format=just-symbols", str(obj)], check=True, capture_output=True,
                                 text=True, timeout=30).stdout.split()
        self.assertNotIn("15AV1CameraClient", "".join(symbols))
        self.assertFalse([symbol for symbol in symbols if symbol.startswith("vk")])
        self.assertIn("mmap", symbols)

    def test_gpu_pixel_path_references_no_sdk_symbol_and_links_no_vulkan(self):
        probe = subprocess.run(["g++", "-std=c++17", "-x", "c++", "-fsyntax-only", "-"], input="#include <vulkan/vulkan.h>\n",
                               capture_output=True, text=True, timeout=60)
        cached = ROOT / ".cache/vulkan-headers/include"
        includes = [] if probe.returncode == 0 else [f"-I{cached}"]
        if probe.returncode and not (cached / "vulkan/vulkan.h").is_file():
            self.skipTest("Vulkan headers required (libvulkan-dev or scripts/build_capture_gpu_check.sh)")
        obj = self.work / "camera_pixels_gpu.o"
        self.compile(ROOT / "tools/camera_pixels_gpu.cpp", obj, *includes, "-c",
                     flags=["-std=c++17", "-Wall", "-Wextra", "-Wpedantic", "-Werror"])
        symbols = subprocess.run(["nm", "-u", "--format=just-symbols", str(obj)], check=True, capture_output=True,
                                 text=True, timeout=30).stdout.split()
        self.assertNotIn("15AV1CameraClient", "".join(symbols))
        # The loader is opened at run time: no Vulkan entry point is an undefined symbol.
        self.assertFalse([symbol for symbol in symbols if symbol.startswith("vk")], symbols)
        self.assertTrue([symbol for symbol in symbols if symbol.startswith("dlopen")], symbols)

    def test_sha256_matches_hashlib(self):
        fixture = self.work / "sha256"
        self.compile(ROOT / "tests/fixtures/camera_sha256_fixture.cpp", fixture)
        for data in (b"", b"abc", b"a" * 55, b"a" * 56, b"a" * 63, b"a" * 64, b"a" * 65, bytes(range(256)) * 17,
                     b"The quick brown fox jumps over the lazy dog"):
            with self.subTest(length=len(data)):
                result = subprocess.run([str(fixture)], input=data, capture_output=True, timeout=30)
                self.assertEqual(result.stdout.decode().strip(), hashlib.sha256(data).hexdigest())


@unittest.skipUnless(shutil.which("sha256sum") and shutil.which("realpath"), "coreutils required for the build script")
class BuildScriptRefusals(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name).resolve()
        self.assertNotEqual(ROOT, self.work)
        self.assertNotIn(ROOT, self.work.parents)
        self.artifacts_before = (ROOT / "artifacts/camera-helper").exists()

    def tearDown(self):
        self.assertEqual((ROOT / "artifacts/camera-helper").exists(), self.artifacts_before)

    def fake_sdk(self, name="sdk", release=True, break_hash=False, change_header=False):
        """A directory shaped like the SDK drop, with a dummy archive and a manifest matching its files."""
        sdk = self.work / name
        (sdk / "lib").mkdir(parents=True)
        for file_name in (*SDK_HEADERS, "AV1CameraSDK-LICENSE.md"):
            shutil.copyfile(VENDORED / file_name, sdk / file_name)
        if change_header:
            with (sdk / "AV1CameraClient.h").open("ab") as handle:
                handle.write(b"// a local edit\n")
        (sdk / "lib/libAV1CameraClient.a").write_bytes(b"!<arch>\nnot a real archive; test fixture only\n")
        files = {file_name: hashlib.sha256((sdk / file_name).read_bytes()).hexdigest()
                 for file_name in (*SDK_HEADERS, "AV1CameraSDK-LICENSE.md", "lib/libAV1CameraClient.a")}
        if break_hash:
            files["AV1CameraIPCProtocol.h"] = "0" * 64
        if release:
            (sdk / "release.json").write_text(json.dumps({"source_revision": SOURCE_REVISION,
                                                          "agreement_version": AGREEMENT_VERSION, "files": files}))
        return sdk

    def run_script(self, *args, env=None):
        return subprocess.run(["bash", str(SCRIPT), *map(str, args)], cwd=ROOT, capture_output=True, text=True,
                              env=env, timeout=300)

    def test_check_sdk_accepts_a_matching_directory_without_building(self):
        result = self.run_script("--sdk", self.fake_sdk(), "--check-sdk")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SDK verified", result.stdout)
        self.assertIn("Nothing was built", result.stdout)
        self.assertEqual(sorted(path.name for path in self.work.iterdir()), ["sdk"])

    def test_a_directory_inside_the_checkout_is_refused_directly_and_through_a_symlink(self):
        link = self.work / "link-into-checkout"
        link.symlink_to(ROOT / "tools", target_is_directory=True)
        for directory in (ROOT, ROOT / "tools", ROOT / "third_party/arcturus-camera-sdk", link):
            with self.subTest(directory=str(directory)):
                result = self.run_script("--sdk", directory, "--check-sdk")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("outside the work tree", result.stderr)

    def test_a_missing_or_mismatched_manifest_is_refused(self):
        result = self.run_script("--sdk", self.fake_sdk("no-manifest", release=False), "--check-sdk")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release.json", result.stderr)
        result = self.run_script("--sdk", self.fake_sdk("bad-hash", break_hash=True), "--check-sdk")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match", result.stderr)
        self.assertNotIn("SDK verified", result.stdout)
        result = self.run_script("--sdk", self.work / "absent", "--check-sdk")
        self.assertNotEqual(result.returncode, 0)

    def test_a_header_that_differs_from_the_vendored_copy_is_refused(self):
        result = self.run_script("--sdk", self.fake_sdk("edited", change_header=True), "--check-sdk")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("differs from the vendored copy", result.stderr)

    def test_a_real_build_is_refused_off_aarch64(self):
        stubs = self.work / "stubs"
        stubs.mkdir()
        uname = stubs / "uname"
        uname.write_text('#!/bin/sh\ncase "$1" in -m) echo x86_64 ;; *) echo Linux ;; esac\n')
        uname.chmod(uname.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        env = dict(os.environ, PATH=f"{stubs}{os.pathsep}{os.environ.get('PATH', '')}")
        result = self.run_script("--sdk", self.fake_sdk(), env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("aarch64", result.stderr)
        self.assertIn("SDK verified", result.stdout)

    def test_usage_errors(self):
        self.assertEqual(self.run_script().returncode, 64)
        self.assertEqual(self.run_script("--nonsense").returncode, 64)
        self.assertEqual(self.run_script("--help").returncode, 0)


if __name__ == "__main__":
    unittest.main()
