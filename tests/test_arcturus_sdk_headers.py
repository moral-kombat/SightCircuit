"""The vendored Arcturus camera SDK headers stay unmodified, licensed and library-free.

Section 2 of the SDK agreement permits redistributing the two public headers
only with the agreement and their copyright notices preserved, and never the
Client Library. These tests pin the copies to the release checksums recorded in
the directory's README, keep the agreement beside them, keep
``libAV1CameraClient.a`` out of Git, and check that the headers compile on their
own as the C++11, exception-free, RTTI-free interface they declare.
"""
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VENDORED = ROOT / "third_party/arcturus-camera-sdk"
CLIENT_LIBRARY = "libAV1CameraClient.a"
# The release manifest's checksums for source revision 536217ebbc0456df5db53658409b048cdd272785.
CHECKSUMS = {
    "AV1CameraClient.h": "00c92c19810068c436e96cb1df7ee09327c0f60493cb050a008f71a305b5f6f6",
    "AV1CameraIPCProtocol.h": "abdc957cc9dfcd59cbab124e1b8e33d82a79e6415662e0bc3f62d83c145c8f64",
    "AV1CameraSDK-LICENSE.md": "317c0bd59f40da6fb42bccd96887fb684e26e412772445f5422063435d5aa142",
}
NOTICE = "// Copyright (c) 2026 Arcturus Industries LLC. All rights reserved."


class VendoredHeaderTests(unittest.TestCase):
    def test_only_the_headers_agreement_and_provenance_are_vendored(self):
        self.assertEqual(sorted(path.name for path in VENDORED.iterdir()), sorted([*CHECKSUMS, "README.md"]))

    def test_copies_match_the_release_checksums(self):
        for name, expected in CHECKSUMS.items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((VENDORED / name).read_bytes()).hexdigest(), expected)

    def test_readme_records_the_pinned_checksums(self):
        readme = (VENDORED / "README.md").read_text()
        for name, expected in CHECKSUMS.items():
            with self.subTest(name=name):
                self.assertRegex(readme, rf"\| `{re.escape(name)}` \| `{expected}` \|")

    def test_headers_keep_their_notice_and_point_to_the_agreement(self):
        for name in ("AV1CameraClient.h", "AV1CameraIPCProtocol.h"):
            with self.subTest(name=name):
                text = (VENDORED / name).read_text()
                self.assertTrue(text.startswith(NOTICE + "\n"))
                self.assertIn("(AV1CameraSDK-LICENSE.md)", text)

    def test_the_client_library_is_ignored_and_never_tracked(self):
        for path in (CLIENT_LIBRARY, f"third_party/arcturus-camera-sdk/lib/{CLIENT_LIBRARY}",
                     f".cache/arcturus-sdk/lib/{CLIENT_LIBRARY}"):
            with self.subTest(path=path):
                result = subprocess.run(["git", "check-ignore", "-q", "--no-index", "--", path], cwd=ROOT,
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, f"{path} is not ignored: {result.stderr}")
        tracked = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True,
                                 timeout=30).stdout.decode().split("\0")
        self.assertEqual([path for path in tracked if Path(path).name == CLIENT_LIBRARY], [])

    @unittest.skipUnless(shutil.which("g++"), "C++ compiler required to compile the vendored headers")
    def test_headers_compile_alone_as_the_declared_cxx11_interface(self):
        # The protocol header's own static_asserts pin the wire header and reply bounds.
        source = '#include "AV1CameraClient.h"\nint main() { return AV1CameraClient().isConnected() ? 1 : 0; }\n'
        with tempfile.TemporaryDirectory() as temporary:
            unit = Path(temporary) / "headers.cpp"
            unit.write_text(source)
            subprocess.run(["g++", "-std=c++11", "-fno-exceptions", "-fno-rtti", "-Wall", "-Wextra", "-Werror",
                            "-fsyntax-only", f"-I{VENDORED}", str(unit)], check=True, capture_output=True,
                           timeout=30)


if __name__ == "__main__":
    unittest.main()
