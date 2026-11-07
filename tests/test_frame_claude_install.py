"""Phase 0 install script: syntax, stage flow and abort detection with a fake ``claude``.

The signature stage runs against locally generated GnuPG keys and a fake ``curl``.
"""
import contextlib
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


def pause(name):
    """A pause, in a fake or a script copy, that a test signals: a child that writes its own pid to $HOME/<name> (a
    temporary file, then mv, so the marker never holds part of the pid) and then execs sleep 30. A test signals it only
    once that pid runs sleep (SignatureStageTests.wait_paused), which catches no signal. Until the child execs sleep,
    bash catches INT in it: an INT that lands after bash last looked for one is only noted and then lost at the exec,
    so the sleep would run its full 30 s, and the test would see the stage go on, or take 30 s longer."""
    return f'( echo $BASHPID > "$HOME/{name}.tmp" && mv "$HOME/{name}.tmp" "$HOME/{name}"; exec sleep 30 )'


# Serves every download from $FAKE_DOWNLOADS by file name. With PAUSE_DOWNLOAD set to a file name, pauses (see pause)
# before serving that file, so a test can interrupt the downloads there.
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
if [ -n "${PAUSE_DOWNLOAD:-}" ] && [ "${url##*/}" = "$PAUSE_DOWNLOAD" ]; then
  @pause@
fi
cp "$FAKE_DOWNLOADS/${url##*/}" "$out"
""".replace("@pause@", pause("downloading"))
# Records each call with the keyring it was for. Runs the real gpgconf (REAL) only for a keyring in the test's
# temporary home, never for the default one. With PAUSE_GPGCONF set, "--kill" first waits for $HOME/release. With
# HANG_GPGCONF set, it hangs after its work, ignoring INT, TERM and HUP, for up to 20 s.
FAKE_GPGCONF = """#!/usr/bin/env bash
printf '%s|%s\\n' "$*" "${GNUPGHOME:-}" >> "$HOME/gpgconf.log"
if [ -n "${PAUSE_GPGCONF:-}" ] && [ "$1" = --kill ]; then
  : > "$HOME/dropping"
  for _ in $(seq 600); do [ -e "$HOME/release" ] && break; sleep 0.1; done
fi
case "${GNUPGHOME:-}" in "$HOME"/?*) ;; *) exit 0 ;; esac
[ -n "${HANG_GPGCONF:-}" ] || exec REAL "$@"
REAL "$@"
trap '' INT TERM HUP
for _ in $(seq 200); do sleep 0.1; done
"""
# Records each call that removes something in the test's temporary directory in $HOME/rm.log. With PAUSE_RM set to a
# directory, waits for $HOME/release before removing anything inside it; then runs rm (REAL).
FAKE_RM = """#!/usr/bin/env bash
for arg; do
  case "$arg" in "$HOME/tmp"/?*) printf '%s\\n' "$*" >> "$HOME/rm.log"; break ;; esac
done
for arg; do
  case "$arg" in "${PAUSE_RM:-/nonexistent}"/?*)
    : > "$HOME/dropping"
    for _ in $(seq 600); do [ -e "$HOME/release" ] && break; sleep 0.1; done ;;
  esac
done
exec REAL "$@"
"""
# Records each call with the keyring it was for. With PAUSE_VERIFY set, pauses (see pause) before verifying, so a test
# can interrupt the stage there; then runs the real gpg.
FAKE_GPG = """#!/usr/bin/env bash
printf '%s|%s\\n' "$*" "${GNUPGHOME:-}" >> "$HOME/gpg.log"
if [ -n "${PAUSE_VERIFY:-}" ] && [[ " $* " == *" --verify "* ]]; then
  @pause@
fi
exec REAL "$@"
""".replace("@pause@", pause("verifying"))


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
        cls.gnupg = cls.keys / "g"
        cls.gnupg.mkdir(mode=0o700)
        # The agent's socket directory: outside the keys where /run/user/<uid> exists (asking for it creates it).
        cls.socketdir = Path(subprocess.run(["gpgconf", "--homedir", str(cls.gnupg), "--list-dirs", "socketdir"],
                                            capture_output=True, text=True, timeout=30).stdout.strip())
        cls.addClassCleanup(cls.drop_keys)
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
    def drop_keys(cls):
        """Stop the keys' agent, then remove its socket directory, then the keys; fail if anything is left.

        In another order ``gpgconf`` finds no agent to stop, or makes the socket directory again after it was removed.
        """
        for command in (["--kill", "all"], ["--remove-socketdir"]):
            subprocess.run(["gpgconf", "--homedir", str(cls.gnupg), *command], capture_output=True, timeout=30)
        shutil.rmtree(cls.keys, ignore_errors=True)
        left = [str(path) for path in (cls.keys, cls.socketdir) if path.exists()]
        left += [f"process {pid}" for pid in cls.processes_using(cls.gnupg)]
        if left:
            raise AssertionError(f"the test keyring left {', '.join(left)} behind")

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
        self.fakebin("rm", FAKE_RM.replace("REAL", shlex.quote(shutil.which("rm"))))
        self.addCleanup(self.stop_agents_of_your_own_home)
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
        """Every gpg call used the throwaway keyring, its agent was stopped once, by gpgconf for that keyring only, and
        nothing is left."""
        log = self.home / "gpgconf.log"
        calls = log.read_text().splitlines() if log.exists() else []
        keyring = calls[0].partition("|")[2] if calls else ""
        self.assertTrue(keyring.startswith(f"{self.home / 'tmp'}/"), f"gpgconf ran for another keyring: {calls}")
        self.assertEqual(calls, [f"--kill all|{keyring}", f"--remove-socketdir|{keyring}"],
                         "gpgconf did not stop the throwaway keyring's agent exactly once")
        gpg_log = self.home / "gpg.log"
        gpg_calls = gpg_log.read_text().splitlines() if gpg_log.exists() else []
        self.assertTrue(gpg_calls, "gpg never ran")
        self.assertEqual({call.rpartition("|")[2] for call in gpg_calls}, {keyring},
                         f"a gpg call did not use the throwaway keyring: {gpg_calls}")
        self.assertTempEmpty()

    def assertTempEmpty(self):
        self.assertEqual(list((self.home / "tmp").iterdir()), [], "the stage's temporary directory was left behind")
        self.assertEqual(self.processes_using(self.home / "tmp"), [], "the throwaway keyring's agent was left running")
        self.assertFalse((self.home / ".gnupg").exists(), "gpg used your own GnuPG home")
        self.assertEqual(self.processes_using(self.home / ".gnupg"), [], "an agent runs for your own GnuPG home")

    def stop_agents_of_your_own_home(self):
        """Should gpg ever use the test home's own GnuPG home, its agent must not outlive the test."""
        for pid in self.processes_using(self.home / ".gnupg", seconds=0):
            with contextlib.suppress(OSError):
                os.kill(int(pid), signal.SIGKILL)

    @contextlib.contextmanager
    def signature_run(self, *markers, script=None, **extra_env):
        """The signature stage for the pinned key (in ``script``, default a fresh copy), in its own process group:
        signalled as a whole, like Ctrl-C or a dropped SSH connection. Afterwards it is stopped, with whatever it left
        running (see kill_leftovers), and its reports and the fakes' ``markers`` are removed."""
        shutil.copy(self.keys / "pinned.sig", self.downloads / "manifest.json.sig")
        env = self.script_env(FAKE_OK, FAKE_DOWNLOADS=str(self.downloads), TMPDIR=str(self.home / "tmp"), **extra_env)
        script = script or self.pinned_script("pinned")
        process = subprocess.Popen(["bash", str(script), "signature"], env=env, cwd=self.home,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            yield process
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=60)
            self.kill_leftovers()
            self.reset(*markers)

    def kill_leftovers(self):
        """SIGKILL every process whose command line names the test home: a cleanup gpgconf that runs under timeout, in
        timeout's own process group, or the throwaway keyring's agent, so that neither outlives a failing subtest.
        timeout exits once its child is gone."""
        for pid in self.processes_using(self.home, seconds=0):
            if int(pid) != os.getpid():
                with contextlib.suppress(OSError):
                    os.kill(int(pid), signal.SIGKILL)

    def wait_for(self, name, process):
        """Wait (generously, for a loaded machine) until a fake creates ``$HOME/<name>`` or the script exits."""
        deadline = time.monotonic() + 60
        while not (self.home / name).exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue((self.home / name).exists(), f"the script never reached the point where it creates {name}")

    def wait_paused(self, name, process):
        """Wait (as generously) until the pause that wrote its pid to ``$HOME/<name>`` runs sleep, and return that pid.
        From then on the pause takes every signal with the default handling (see pause)."""
        self.wait_for(name, process)
        pid = int((self.home / name).read_text())

        def sleeping():
            try:
                return Path(f"/proc/{pid}/comm").read_text() == "sleep\n"
            except OSError:
                return False
        deadline = time.monotonic() + 60
        while not sleeping() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(sleeping(), f"the pause that wrote {name} never ran sleep")
        return pid

    def wait_catching(self, process):
        """Wait until the script catches INT, TERM and HUP again. drop_keyring ignores them while it starts the
        cleanup, and the cleanup's fakes can create their marker before the script is past that; a signal then is
        lost, and the test would fail on correct code."""
        wanted = sum(1 << (number - 1) for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP))

        def catching():
            try:
                lines = Path(f"/proc/{process.pid}/status").read_text().splitlines()
            except OSError:
                return False
            masks = dict(line.split(":", 1) for line in lines if line.startswith(("SigIgn:", "SigCgt:")))
            return int(masks["SigCgt"], 16) & wanted == wanted and not int(masks["SigIgn"], 16) & wanted
        deadline = time.monotonic() + 60
        while not catching() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(catching(), "the script never caught INT, TERM and HUP again")

    def reset(self, *names):
        for leftover in ("out", "gpgconf.log", "gpg.log", "rm.log", *names):
            shutil.rmtree(self.home / leftover, ignore_errors=True)
            (self.home / leftover).unlink(missing_ok=True)

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
                self.reset()

    def test_an_interrupted_stage_removes_the_keyring_and_stops_its_agent(self):
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=number.name), self.signature_run("verifying", PAUSE_VERIFY="1") as process:
                self.wait_paused("verifying", process)
                os.killpg(process.pid, number)
                self.assertEqual(process.wait(timeout=60), 128 + number)
                self.assertIn("STOPPED: signature stage interrupted", self.summary())
                self.assertKeyringDropped()

    def test_an_interrupted_download_removes_the_temporary_directory(self):
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            # Paused before the manifest: the key file is already in the temporary directory.
            with self.subTest(signal=number.name), \
                    self.signature_run("downloading", PAUSE_DOWNLOAD="manifest.json") as process:
                self.wait_paused("downloading", process)
                os.killpg(process.pid, number)
                self.assertEqual(process.wait(timeout=60), 128 + number)
                self.assertIn("STOPPED: signature stage interrupted", self.summary())
                self.assertFalse((self.home / "gpgconf.log").exists(), "gpgconf ran before there was a keyring")
                self.assertTempEmpty()

    def test_a_signal_while_the_keyring_is_dropped_neither_repeats_nor_widens_the_cleanup(self):
        # The cleanup must finish once, with gpgconf only ever for the throwaway keyring: a re-entered cleanup ran
        # after GNUPGHOME was unset, so gpgconf would stop the agent of your own GnuPG home.
        for held in ({"PAUSE_GPGCONF": "1"}, {"PAUSE_RM": str(self.home / "tmp")}):
            for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                with self.subTest(pause=next(iter(held)), signal=number.name), \
                        self.signature_run("dropping", "release", **held) as process:
                    self.wait_for("dropping", process)
                    self.wait_catching(process)
                    os.killpg(process.pid, number)
                    (self.home / "release").touch()
                    self.assertEqual(process.wait(timeout=60), 128 + number)
                    self.assertIn("STOPPED: signature stage interrupted", self.summary())
                    self.assertKeyringDropped()
                    # Removing the directory again would show a second cleanup; gpgconf would not, as the keyring is gone.
                    work = Path((self.home / "gpgconf.log").read_text().splitlines()[0].partition("|")[2]).parent
                    self.assertEqual((self.home / "rm.log").read_text().splitlines(), [f"-rf {work}"],
                                     "the cleanup did not run exactly once")

    def test_signals_in_quick_succession_still_remove_the_keyring(self):
        # Ctrl-C held down, or a dropped SSH connection (the login shell, then the kernel, each send a hangup): a
        # signal right behind the first must not end the cleanup before it ignores signals. Bounded, with a pause
        # between signals, so bash is not kept busy in its trap handlers.
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=number.name), self.signature_run("verifying", PAUSE_VERIFY="1") as process:
                self.wait_paused("verifying", process)
                deadline = time.monotonic() + 3
                while process.poll() is None and time.monotonic() < deadline:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, number)
                    time.sleep(0.0005)
                self.assertEqual(process.wait(timeout=60), 128 + number)
                self.assertIn("STOPPED: signature stage interrupted", self.summary())
                self.assertKeyringDropped()

    def test_the_signal_that_stopped_the_stage_decides_the_exit_code(self):
        # The first signal stops the stage at the verification; a second one while the cleanup removes the
        # temporary directory is only noted.
        for first, second in ((signal.SIGTERM, signal.SIGINT), (signal.SIGHUP, signal.SIGTERM),
                              (signal.SIGINT, signal.SIGHUP)):
            with self.subTest(first=first.name, second=second.name), \
                    self.signature_run("verifying", "dropping", "release", PAUSE_VERIFY="1",
                                       PAUSE_RM=str(self.home / "tmp")) as process:
                self.wait_paused("verifying", process)
                os.killpg(process.pid, first)
                self.wait_for("dropping", process)
                self.wait_catching(process)  # Else the second signal could come while it is ignored.
                os.killpg(process.pid, second)
                (self.home / "release").touch()
                self.assertEqual(process.wait(timeout=60), 128 + first)
                self.assertIn("STOPPED: signature stage interrupted", self.summary())
                self.assertKeyringDropped()

    def test_a_signal_after_the_cleanup_is_not_lost(self):
        # Without a noted signal, drop_keyring restores the default handling before it looks for one a last time: a
        # signal after that look stops the script instead of being noted where nothing reads it. A pause right after
        # the look makes that moment wide enough to hit; the signal comes once the pause runs sleep (see pause).
        check = '[ -z "$stop" ] || { note "- STOPPED: signature stage interrupted"; exit "$stop"; }'
        script = self.pinned_script("pinned")
        text = script.read_text()
        self.assertEqual(text.count(check), 1)
        script.write_text(text.replace(check, check + "\n    " + pause("checked")))
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=number.name), self.signature_run("checked", script=script) as process:
                self.wait_paused("checked", process)
                os.killpg(process.pid, number)
                self.assertEqual(process.wait(timeout=60), -number)
                self.assertNotIn("installed binary matches", self.summary())
                self.assertKeyringDropped()

    def test_a_signal_after_the_cleanup_of_an_interrupted_stage_is_still_only_noted(self):
        # With a signal noted, drop_keyring keeps its noting traps until it exits with that signal's code, so a second
        # signal after the cleanup, right before the stage stops, is only noted too. A pause right before the stop
        # check makes that moment wide enough to hit. The pause comes after drop_keyring set its noting traps, and the
        # second signal comes once the pause runs sleep (see pause), so it always finds both in place: it ends the
        # sleep, and the script, which only notes it, goes on to the check at once.
        check = '[ -z "$stop" ] || { note "- STOPPED: signature stage interrupted"; exit "$stop"; }'
        script = self.pinned_script("pinned")
        text = script.read_text()
        self.assertEqual(text.count(check), 1)
        script.write_text(text.replace(check, pause("checking") + "\n    " + check))
        for first, second in ((signal.SIGTERM, signal.SIGINT), (signal.SIGHUP, signal.SIGTERM),
                              (signal.SIGINT, signal.SIGHUP)):
            with self.subTest(first=first.name, second=second.name), \
                    self.signature_run("verifying", "checking", script=script, PAUSE_VERIFY="1") as process:
                self.wait_paused("verifying", process)
                os.killpg(process.pid, first)
                self.wait_paused("checking", process)
                os.killpg(process.pid, second)
                self.assertEqual(process.wait(timeout=60), 128 + first)
                self.assertIn("STOPPED: signature stage interrupted", self.summary())
                self.assertKeyringDropped()

    def test_a_hanging_gpgconf_cannot_keep_the_stage_waiting(self):
        # The cleanup ignores INT, TERM and HUP so that it finishes, so each gpgconf in it is bounded: here one that
        # hangs after its work, ignoring TERM, is killed once its timeout (shortened to 1 s plus 1 s) is over.
        script = self.pinned_script("pinned")
        text = script.read_text()
        self.assertEqual(text.count("timeout -k 5 20 gpgconf "), 2)
        script.write_text(text.replace("timeout -k 5 20 gpgconf ", "timeout -k 1 1 gpgconf "))
        shutil.copy(self.keys / "pinned.sig", self.downloads / "manifest.json.sig")
        started = time.monotonic()
        result, summary = self.run_script(FAKE_OK, "signature", script=script, HANG_GPGCONF="1",
                                          FAKE_DOWNLOADS=str(self.downloads), TMPDIR=str(self.home / "tmp"))
        self.assertLess(time.monotonic() - started, 20, "the stage waited for a hanging gpgconf")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: installed binary matches the signed manifest", summary)
        self.assertKeyringDropped()

    def test_a_failed_download_warns_and_removes_the_temporary_directory(self):
        # No manifest.json.sig to serve: the third download fails, as it would on a Frame without network.
        result, summary = self.run_script(FAKE_OK, "signature", script=self.pinned_script("pinned"),
                                          FAKE_DOWNLOADS=str(self.downloads), TMPDIR=str(self.home / "tmp"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("WARN: could not download the key or manifest for 9.9.9", summary)
        self.assertFalse((self.home / "gpgconf.log").exists(), "gpgconf ran before there was a keyring")
        self.assertTempEmpty()

    def test_a_key_file_without_the_pinned_key_fails_and_drops_the_keyring(self):
        # The unmodified script pins Anthropic's key, which is not in the locally generated key file.
        shutil.copy(self.keys / "pinned.sig", self.downloads / "manifest.json.sig")
        result, summary = self.run_script(FAKE_OK, "signature", FAKE_DOWNLOADS=str(self.downloads),
                                          TMPDIR=str(self.home / "tmp"))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FAIL: release key fingerprint does not match", summary)
        self.assertNotIn("manifest signature", summary)
        self.assertKeyringDropped()

    def test_a_failed_mktemp_skips_the_stage_before_any_download(self):
        # This curl only logs and fails. It must not copy: tests may run as root, and without the check its -o would
        # be /key.asc.
        self.fakebin("curl", '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$HOME/curl.log"\nexit 22\n')
        result, summary = self.run_script(FAKE_OK, "signature", script=self.pinned_script("pinned"),
                                          FAKE_DOWNLOADS=str(self.downloads), TMPDIR=str(self.home / "missing"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("WARN: could not create a temporary directory; signature check skipped", summary)
        self.assertFalse((self.home / "curl.log").exists(), "the stage downloaded without a temporary directory")
        self.assertFalse((self.home / "gpgconf.log").exists(), "gpgconf ran without a keyring")

    def test_a_signal_after_the_stage_still_stops_the_script(self):
        # drop_keyring only notes a signal while it cleans up; at its end it restores the default handling, so Ctrl-C
        # (INT), TERM or HUP in a later stage still stops the script. The downloads fail here (no manifest.json.sig).
        fake = FAKE_OK.replace("  -p) printf 'aarch64\\nframe\\n' ;;",
                               '  -p) echo $$ > "$HOME/pid"; mv "$HOME/pid" "$HOME/smoking"; exec sleep 60 ;;')
        self.assertNotEqual(fake, FAKE_OK)
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=number.name):
                env = self.script_env(fake, FAKE_DOWNLOADS=str(self.downloads), TMPDIR=str(self.home / "tmp"))
                process = subprocess.Popen(["bash", str(self.pinned_script("pinned")), "signature", "smoke", "record"],
                                           env=env, cwd=self.home, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, start_new_session=True)
                try:
                    claude = self.wait_paused("smoking", process)
                    os.killpg(process.pid, number)
                    # The fake claude runs in timeout's own process group, which the group signal does not reach; it
                    # ends here, so the smoke step returns and the script acts on the signal now.
                    os.kill(claude, signal.SIGTERM)
                    self.assertEqual(process.wait(timeout=60), -number)
                    self.assertIn("WARN: could not download the key or manifest", self.summary())
                    self.assertNotIn("summary written", self.summary())
                    self.assertTempEmpty()
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=60)
                    with contextlib.suppress(OSError, ValueError):
                        os.kill(int((self.home / "smoking").read_text()), signal.SIGKILL)
                    self.kill_leftovers()
                    self.reset("smoking")


if __name__ == "__main__":
    unittest.main()
