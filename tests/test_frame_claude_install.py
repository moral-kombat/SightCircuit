"""Phase 0 install script: syntax, stage flow and abort detection with a fake ``claude``.

The signature stage runs against locally generated GnuPG keys and a fake ``curl``.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "frame_claude_install.sh"
RELEASE_KEY = "31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE"

FAKE_OK = """#!/usr/bin/env bash
case "$1" in
  --version) echo "9.9.9 (Claude Code)" ;;
  doctor) echo "Install: healthy" ;;
  -p) printf 'aarch64\\nframe\\n' ;;
  *) exit 3 ;;
esac
"""
FAKE_ABORT = """#!/usr/bin/env bash
echo Aborted >&2
kill -ABRT $$
"""
FAKE_LOGGED_OUT = """#!/usr/bin/env bash
case "$1" in
  --version) echo "9.9.9 (Claude Code)" ;;
  -p) echo "Invalid API key. Please run /login" >&2; exit 1 ;;
esac
"""
FAKE_HANG = """#!/usr/bin/env bash
case "$1" in
  --version) exec sleep 60 ;;
esac
"""
# More output than a pipe buffer holds, with the line the script looks for first.
FAKE_LOUD = """#!/usr/bin/env bash
pad() { head -c 300000 /dev/zero | tr '\\0' x; echo; }
case "$1" in
  --version) echo Aborted; pad; exit 1 ;;
  -p) echo aarch64; pad ;;
esac
"""
# Serves every download from $FAKE_DOWNLOADS by file name.
FAKE_CURL = """#!/usr/bin/env bash
out=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    --max-time|--proto|--proto-redir) shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
[ -n "$out" ] || exit 0
cp "$FAKE_DOWNLOADS/${url##*/}" "$out"
"""
# Records each call with the keyring it was for, then runs the real gpgconf (REAL).
FAKE_GPGCONF = """#!/usr/bin/env bash
printf '%s|%s\\n' "$*" "${GNUPGHOME:-}" >> "$HOME/gpgconf.log"
exec REAL "$@"
"""
# With PAUSE_VERIFY set, waits before verifying, so a test can interrupt the stage there; then runs the real gpg.
FAKE_GPG = """#!/usr/bin/env bash
if [ -n "${PAUSE_VERIFY:-}" ] && [[ " $* " == *" --verify "* ]]; then
  : > "$HOME/verifying"; sleep 30
fi
exec REAL "$@"
"""


def short_tempdir(prefix):
    """A new temporary directory with a short path: gpg-agent's socket path must fit in about 100 bytes."""
    base = tempfile.gettempdir()
    return Path(tempfile.mkdtemp(prefix=prefix, dir=base if len(base) <= 20 else "/tmp"))


class ScriptHarness:
    """Runs the script (or a copy) with a fake ``claude`` under a temporary home; YES=1, no breakpoints."""

    def setUp(self):
        self.home = short_tempdir("fjhome-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        (self.home / ".local/bin").mkdir(parents=True)

    def script_env(self, fake, **extra_env):
        binary = self.home / ".local/bin/claude"
        binary.write_text(fake)
        binary.chmod(0o755)
        return {"PATH": f"{self.home / 'fakebin'}:{self.home / '.local/bin'}:/usr/bin:/bin", "HOME": str(self.home),
                "YES": "1", "OUT": str(self.home / "out"), "STEP_TIMEOUT": "20", **extra_env}

    def summary(self):
        return (self.home / "out/summary.md").read_text() if (self.home / "out/summary.md").exists() else ""

    def run_script(self, fake, *stages, script=SCRIPT, timeout=120, **extra_env):
        # Runs in the temporary home, so a fake that aborts cannot leave a core file in the checkout.
        result = subprocess.run(["bash", str(script), *stages], env=self.script_env(fake, **extra_env),
                                cwd=self.home, capture_output=True, text=True, timeout=timeout)
        return result, self.summary()

    def fakebin(self, name, text):
        path = self.home / "fakebin" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
        path.chmod(0o755)


@unittest.skipUnless(shutil.which("bash") and shutil.which("timeout"), "bash and coreutils timeout required")
class FrameClaudeInstallTests(ScriptHarness, unittest.TestCase):
    def test_syntax(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def test_verify_smoke_and_record_pass_with_a_working_binary(self):
        result, summary = self.run_script(FAKE_OK, "path", "verify", "login", "smoke", "record")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for expected in ("PASS: claude on PATH", "PASS: claude --version: 9.9.9 (Claude Code)",
                         "PASS: claude doctor exited 0", "SKIPPED: login", "reported aarch64",
                         "claude: 9.9.9 (Claude Code)", "0 failure(s)"):
            self.assertIn(expected, summary)
        self.assertEqual((self.home / "out/doctor.txt").read_text(), "Install: healthy\n")

    def test_abort_at_start_stops_with_the_known_issue(self):
        result, summary = self.run_script(FAKE_ABORT, "verify", "smoke")
        self.assertEqual(result.returncode, 1)
        self.assertIn("reported aarch64 abort", summary)
        self.assertNotIn("headless", summary)  # stopped before the smoke stage
        self.assertFalse((self.home / "out/smoke.txt").exists())

    def test_logged_out_smoke_points_to_login(self):
        result, summary = self.run_script(FAKE_LOGGED_OUT, "smoke")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not signed in", summary)

    def test_long_output_is_still_classified(self):
        # Under pipefail, "printf ... | grep -q" fails when grep exits before printf has written everything.
        result, summary = self.run_script(FAKE_LOUD, "smoke")
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
        self.assertIn("PASS: the agent ran a command on this machine and reported aarch64", summary)
        result, summary = self.run_script(FAKE_LOUD, "verify")
        self.assertEqual(result.returncode, 1)
        self.assertIn("reported aarch64 abort", summary)

    def test_downloads_follow_redirects_only_over_https(self):
        code = [line for line in SCRIPT.read_text().splitlines() if not line.lstrip().startswith("#")]
        following = [line for line in code if re.search(r"\bcurl\s+-\w*L", line)]
        self.assertEqual(len(following), 2)  # The installer, and the signature stage's downloads.
        for line in following:
            self.assertIn("--proto =https --proto-redir =https", line)

    def test_version_queries_are_bounded_by_the_step_timeout(self):
        # The signature stage needs gpg and python3 before it asks for the version; stand-ins are enough.
        for name in ("gpg", "python3"):
            self.fakebin(name, "#!/bin/sh\nexit 97\n")
        for stage in ("signature", "record"):
            with self.subTest(stage=stage):
                started = time.monotonic()
                result, summary = self.run_script(FAKE_HANG, stage, timeout=40, STEP_TIMEOUT="2")
                self.assertLess(time.monotonic() - started, 30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("version timed out after 2s", summary)

    def test_unknown_stage_is_refused(self):
        result, _ = self.run_script(FAKE_OK, "uninstall-steamvr")
        self.assertEqual(result.returncode, 2)

    def test_never_uses_sudo_or_service_control(self):
        code = "\n".join(line for line in SCRIPT.read_text().splitlines() if not line.lstrip().startswith("#"))
        for word in ("sudo ", "systemctl", "steamosctl", "steamos-readonly", "pacman"):
            self.assertNotIn(word, code)


@unittest.skipUnless(shutil.which("bash") and shutil.which("timeout") and shutil.which("gpg")
                     and shutil.which("gpgconf"), "bash, coreutils timeout and GnuPG required")
class SignatureStageTests(ScriptHarness, unittest.TestCase):
    """The manifest must carry a good signature by the pinned key, even when the key file carries other keys.

    Keys: ``pinned`` and ``attacker`` (signing primaries), ``revoked`` (revoked after signing), ``expired``
    (made in 2020 with a one-day lifetime) and ``rotated`` (a certify-only primary whose first signing subkey
    was revoked as compromised and replaced by a second).
    """

    BINARY = b"claude 9.9.9 binary"

    @classmethod
    def setUpClass(cls):
        cls.keys = short_tempdir("fjkeys-")
        cls.addClassCleanup(shutil.rmtree, cls.keys, ignore_errors=True)
        cls.gnupg = cls.keys / "g"
        cls.gnupg.mkdir(mode=0o700)
        for command in ("--kill", "--remove-socketdir"):
            cls.addClassCleanup(subprocess.run, ["gpgconf", "--homedir", str(cls.gnupg), command,
                                                 *(["all"] if command == "--kill" else [])],
                                capture_output=True, timeout=30)
        checksum = hashlib.sha256(cls.BINARY).hexdigest()
        manifest = cls.keys / "manifest.json"
        manifest.write_text(json.dumps({"version": "9.9.9", "platforms": {
            platform: {"checksum": checksum} for platform in ("linux-x64", "linux-arm64")}}))
        cls.fingerprints = {}
        for name, usage, faked in (("pinned", "sign", None), ("attacker", "sign", None), ("revoked", "sign", None),
                                   ("expired", "sign", "20200101T000000"), ("rotated", "cert", None)):
            cls.gpg(*(["--faked-system-time", faked] if faked else []), "--passphrase", "", "--quick-gen-key",
                    f"{name} <{name}@example.invalid>", "ed25519", usage, "1d" if faked else "never")
            cls.fingerprints[name] = cls.listing(name)[0][1]
        for _ in range(2):
            cls.gpg("--passphrase", "", "--quick-add-key", cls.fingerprints["rotated"], "ed25519", "sign", "never")
        cls.old_subkey, cls.new_subkey = (fingerprint for _, fingerprint in cls.listing("rotated")[1:])
        for name, signer, faked in (("pinned", cls.fingerprints["pinned"] + "!", None),
                                    ("attacker", cls.fingerprints["attacker"], None),
                                    ("revoked", cls.fingerprints["revoked"], None),
                                    ("expired", cls.fingerprints["expired"], "20200101T120000"),
                                    ("old-subkey", cls.old_subkey + "!", None),
                                    ("new-subkey", cls.new_subkey + "!", None)):
            cls.gpg(*(["--faked-system-time", faked] if faked else []), "--local-user", signer,
                    "--output", str(cls.keys / f"{name}.sig"), "--detach-sign", str(manifest))
        # Revoked only after signing (gpg does not sign with a revoked key): the primary with its
        # revocation certificate, the old subkey as compromised (reason 1).
        certificate = (cls.gnupg / "openpgp-revocs.d" / f"{cls.fingerprints['revoked']}.rev").read_text()
        cls.gpg("--import", input=certificate.replace(":-----BEGIN", "-----BEGIN").encode())
        cls.gpg("--passphrase", "", "--pinentry-mode", "loopback", "--command-fd", "0", "--edit-key",
                cls.fingerprints["rotated"], input=b"key 1\nrevkey\ny\n1\ncompromised\n\ny\nsave\n")
        assert [validity for validity, _ in cls.listing("rotated")] == ["u", "r", "u"], cls.listing("rotated")
        assert cls.listing("revoked")[0][0] == "r"
        (cls.keys / "claude-code.asc").write_bytes(cls.gpg("--armor", "--export").stdout)

    @classmethod
    def gpg(cls, *args, input=None):
        return subprocess.run(["gpg", "--batch", "--homedir", str(cls.gnupg), *args], input=input,
                              capture_output=True, check=True, timeout=60)

    @classmethod
    def listing(cls, name):
        """(validity, fingerprint) of the primary key and then each subkey of ``name``."""
        lines = cls.gpg("--with-colons", "--list-keys", f"{name}@example.invalid").stdout.decode().splitlines()
        keys, validity = [], None
        for fields in (line.split(":") for line in lines):
            if fields[0] in ("pub", "sub"):
                validity = fields[1]
            elif fields[0] == "fpr" and validity is not None:
                keys.append((validity, fields[9]))
                validity = None
        return keys

    def setUp(self):
        super().setUp()
        self.fakebin("curl", FAKE_CURL)
        self.fakebin("python3", f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
        self.fakebin("gpgconf", FAKE_GPGCONF.replace("REAL", shlex.quote(shutil.which("gpgconf"))))
        self.fakebin("gpg", FAKE_GPG.replace("REAL", shlex.quote(shutil.which("gpg"))))
        (self.home / "tmp").mkdir()
        self.downloads = self.home / "downloads"
        self.downloads.mkdir()
        for name in ("claude-code.asc", "manifest.json"):
            shutil.copy(self.keys / name, self.downloads)
        binary = self.home / ".local/share/claude/versions/9.9.9"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(self.BINARY)

    def pinned_script(self, name):
        """A copy of the script that pins the locally generated key ``name`` instead of Anthropic's key."""
        text = SCRIPT.read_text()
        self.assertEqual(text.count(RELEASE_KEY), 1)
        script = self.home / "frame_claude_install.sh"
        script.write_text(text.replace(RELEASE_KEY, self.fingerprints[name]))
        return script

    def run_signature(self, pinned, signature):
        shutil.copy(self.keys / f"{signature}.sig", self.downloads / "manifest.json.sig")
        result, summary = self.run_script(FAKE_OK, "signature", script=self.pinned_script(pinned),
                                          FAKE_DOWNLOADS=str(self.downloads), TMPDIR=str(self.home / "tmp"))
        self.assertKeyringDropped()
        return result, summary

    def assertKeyringDropped(self):
        calls = (self.home / "gpgconf.log").read_text().splitlines()
        for command in ("--kill all", "--remove-socketdir"):
            self.assertTrue([call for call in calls if call.startswith(f"{command}|{self.home / 'tmp'}/")],
                            f"no 'gpgconf {command}' for the throwaway keyring: {calls}")
        self.assertEqual(list((self.home / "tmp").iterdir()), [], "the throwaway keyring was left behind")
        self.assertEqual(self.processes_using(self.home / "tmp"), [], "the throwaway keyring's agent was left running")

    @staticmethod
    def processes_using(path, seconds=5):
        deadline = time.monotonic() + seconds
        while True:
            found = []
            for entry in Path("/proc").glob("[0-9]*"):
                try:
                    if str(path).encode() in (entry / "cmdline").read_bytes():
                        found.append(entry.name)
                except OSError:
                    continue
            if not found or time.monotonic() >= deadline:
                return found
            time.sleep(0.1)

    def log(self):
        return (self.home / "out/commands.log").read_text()

    def test_manifest_signed_by_the_pinned_key_passes(self):
        result, summary = self.run_signature("pinned", "pinned")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for expected in ("PASS: release key fingerprint", "PASS: manifest signature good for 9.9.9",
                         "PASS: installed binary matches the signed manifest", "0 failure(s)",
                         f"checked file: {self.home}/.local/share/claude/versions/9.9.9 "
                         f"(claude on PATH: {self.home}/.local/bin/claude"):
            self.assertIn(expected, summary)

    def test_manifest_signed_by_a_current_subkey_of_the_pinned_key_passes(self):
        # VALIDSIG names the signing subkey, then the pinned primary; the old, revoked subkey does not matter.
        result, summary = self.run_signature("rotated", "new-subkey")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: installed binary matches the signed manifest", summary)
        self.assertRegex(self.log(), rf"VALIDSIG {self.new_subkey} .* {self.fingerprints['rotated']}\n")

    def test_manifest_signed_by_another_key_in_the_key_file_fails(self):
        result, summary = self.run_signature("pinned", "attacker")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("PASS: release key fingerprint", summary)  # The pinned key is in the file ...
        self.assertIn("FAIL: manifest signature did not verify with the pinned release key", summary)
        self.assertNotIn("installed binary matches", summary)  # ... but it did not sign the manifest.
        self.assertIn(f"VALIDSIG {self.fingerprints['attacker']}", self.log())

    def test_a_revoked_or_expired_signing_key_fails(self):
        # gpg exits 0 for these and prints VALIDSIG naming the pinned key; only the status keyword differs.
        for pinned, signature, status in (("revoked", "revoked", "REVKEYSIG"), ("rotated", "old-subkey", "REVKEYSIG"),
                                          ("expired", "expired", "EXPKEYSIG")):
            with self.subTest(pinned=pinned, signature=signature):
                result, summary = self.run_signature(pinned, signature)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("FAIL: manifest signature did not verify with the pinned release key", summary)
                self.assertNotIn("installed binary matches", summary)
                self.assertRegex(self.log(),
                                 rf"\[GNUPG:\] {status} .*\n(.*\n)*\[GNUPG:\] VALIDSIG .*\n(.*\n)*\[exit 0\]")
                for leftover in ("out", "gpgconf.log"):
                    shutil.rmtree(self.home / leftover, ignore_errors=True)
                    (self.home / leftover).unlink(missing_ok=True)

    def test_an_interrupted_stage_removes_the_keyring_and_stops_its_agent(self):
        shutil.copy(self.keys / "pinned.sig", self.downloads / "manifest.json.sig")
        script = self.pinned_script("pinned")
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=number.name):
                env = self.script_env(FAKE_OK, FAKE_DOWNLOADS=str(self.downloads), TMPDIR=str(self.home / "tmp"),
                                      PAUSE_VERIFY="1")
                # Its own process group, signalled as a whole, like Ctrl-C or a dropped SSH connection.
                process = subprocess.Popen(["bash", str(script), "signature"], env=env, cwd=self.home,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           start_new_session=True)
                self.addCleanup(lambda process=process: process.poll() is None and os.killpg(process.pid, 9))
                deadline = time.monotonic() + 30
                while not (self.home / "verifying").exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                os.killpg(process.pid, number)
                self.assertEqual(process.wait(timeout=60), 128 + number)
                self.assertIn("STOPPED: signature stage interrupted", self.summary())
                self.assertKeyringDropped()
                for leftover in ("out", "gpgconf.log", "verifying"):
                    shutil.rmtree(self.home / leftover, ignore_errors=True)
                    (self.home / leftover).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
