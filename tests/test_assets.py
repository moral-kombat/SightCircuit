"""The fixture fetch: a bounded retry on transient network errors, the digest check never retried, no partial file left."""
import contextlib
import hashlib
import http.client
import io
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sightcircuit import assets  # noqa: E402

PAYLOAD = b"fixture bytes " * 300
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()
URL = "https://example.invalid/releases/fixture.bin"


class FakeResponse:
    """What a fake opener returns: a context manager whose ``read`` serves ``payload`` or raises mid-way."""

    def __init__(self, payload=PAYLOAD, fail_after=None, error=None):
        self.data = io.BytesIO(payload)
        self.fail_after = fail_after
        self.error = error
        self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, size):
        self.reads += 1
        if self.fail_after is not None and self.reads > self.fail_after:
            raise self.error
        return self.data.read(size)


class Opener:
    """Fails with ``errors`` in turn (an exception to raise before any byte, or a FakeResponse), then serves the payload."""

    def __init__(self, *outcomes, payload=PAYLOAD):
        self.outcomes = list(outcomes)
        self.payload = payload
        self.calls = []
        self.directory_states = []

    def __call__(self, url, timeout):
        self.calls.append((url, timeout))
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome if outcome is not None else FakeResponse(self.payload)


def http_error(code):
    return urllib.error.HTTPError(URL, code, http.client.responses.get(code, "error"), {}, io.BytesIO(b""))


class FetchFixture(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.directory, ignore_errors=True))
        self.logs, self.warnings, self.sleeps = [], [], []
        self.entry = {"name": "fixture.bin", "url": URL, "sha256": DIGEST}

    def fetch(self, opener, entry=None):
        return assets.fetch([entry or self.entry], self.directory, log=self.logs.append, opener=opener,
                            sleep=self.sleeps.append, warn=self.warnings.append)

    def listing(self):
        return sorted(path.name for path in self.directory.iterdir())


class RetryTests(FetchFixture):
    def test_a_clean_download_is_verified_and_logged_without_a_retry(self):
        opener = Opener()
        self.fetch(opener)
        self.assertEqual(opener.calls, [(URL, assets.TIMEOUT_SECONDS)])
        self.assertEqual((self.directory / "fixture.bin").read_bytes(), PAYLOAD)
        self.assertEqual((self.logs, self.warnings, self.sleeps), (["Downloaded and verified fixture: fixture.bin"], [], []))
        self.assertEqual(self.listing(), ["fixture.bin"])

    def test_each_transient_error_class_is_retried_with_the_backoff_and_named_on_the_error_stream(self):
        cases = {
            "socket.timeout": socket.timeout("timed out"),
            "TimeoutError": TimeoutError("timed out"),
            "ConnectionResetError": ConnectionResetError(104, "Connection reset by peer"),
            "RemoteDisconnected": http.client.RemoteDisconnected("Remote end closed connection without response"),
            "IncompleteRead": http.client.IncompleteRead(b"partial", 100),
            "URLError(timeout)": urllib.error.URLError(socket.timeout("timed out")),
            "URLError(ConnectionResetError)": urllib.error.URLError(ConnectionResetError(104, "reset")),
            "HTTPError 503": http_error(503),
            "HTTPError 502": http_error(502),
            "HTTPError 429": http_error(429),
        }
        for label, error in cases.items():
            with self.subTest(error=label):
                self.setUp()
                opener = Opener(error, error)  # Two failures, then the payload: three attempts.
                self.fetch(opener)
                self.assertEqual(len(opener.calls), 3)
                self.assertEqual(self.sleeps, list(assets.BACKOFF_SECONDS[:2]))
                self.assertEqual(len(self.warnings), 2)
                for number, line in enumerate(self.warnings, 1):
                    self.assertIn("fixture.bin", line)
                    self.assertIn(URL, line)
                    self.assertIn(f"attempt {number} of {assets.ATTEMPTS}", line)
                    self.assertIn(assets.describe(error), line)
                    self.assertIn(f"{assets.BACKOFF_SECONDS[number - 1]:g} s", line)
                self.assertEqual((self.directory / "fixture.bin").read_bytes(), PAYLOAD)
                self.assertEqual(self.listing(), ["fixture.bin"])

    def test_a_failure_in_the_middle_of_the_body_leaves_no_partial_file_and_is_retried(self):
        broken = FakeResponse(fail_after=1, error=http.client.IncompleteRead(b"x" * 10, 500))
        seen = []

        class Watching(Opener):
            def __call__(self, url, timeout):
                seen.append(sorted(path.name for path in self.directory.iterdir()))  # Before every attempt.
                return super().__call__(url, timeout)
        opener = Watching(broken, broken)
        opener.directory = self.directory
        self.fetch(opener)
        self.assertEqual(seen, [[], [], []], "a partial file from the failed attempt was left in the directory")
        self.assertEqual(self.sleeps, list(assets.BACKOFF_SECONDS[:2]))
        self.assertEqual(self.listing(), ["fixture.bin"])

    def test_the_give_up_path_raises_after_the_last_attempt_with_no_file_left(self):
        error = socket.timeout("timed out")
        opener = Opener(*[error] * assets.ATTEMPTS)
        with self.assertRaises(RuntimeError) as raised:
            self.fetch(opener)
        self.assertIn("fixture.bin", str(raised.exception))
        self.assertIn(f"after {assets.ATTEMPTS} attempts", str(raised.exception))
        self.assertIn("socket.timeout" if "socket.timeout" == assets.describe(error) else assets.describe(error),
                      str(raised.exception))
        self.assertIs(raised.exception.__cause__, error)
        self.assertEqual(len(opener.calls), assets.ATTEMPTS)
        self.assertEqual(self.sleeps, list(assets.BACKOFF_SECONDS))
        self.assertEqual(len(self.warnings), assets.ATTEMPTS - 1)
        self.assertEqual(self.listing(), [])

    def test_a_final_error_is_raised_at_once_without_a_retry(self):
        for label, error in (("HTTPError 404", http_error(404)), ("HTTPError 403", http_error(403)),
                             ("PermissionError", PermissionError(13, "denied")), ("ValueError", ValueError("bad url")),
                             ("URLError(str)", urllib.error.URLError("unknown url type: xyz"))):
            with self.subTest(error=label):
                self.setUp()
                opener = Opener(error)
                with self.assertRaises(type(error)):
                    self.fetch(opener)
                self.assertEqual((len(opener.calls), self.sleeps, self.warnings), (1, [], []))
                self.assertEqual(self.listing(), [])

    def test_a_digest_mismatch_fails_closed_once_and_is_not_retried(self):
        opener = Opener(payload=b"not the fixture")
        with self.assertRaises(RuntimeError) as raised:
            self.fetch(opener)
        self.assertIn("failed SHA-256 verification", str(raised.exception))
        self.assertEqual((len(opener.calls), self.sleeps, self.warnings), (1, [], []))
        self.assertEqual(self.listing(), [], "the rejected download was left in the asset directory")

    def test_a_cached_copy_is_verified_and_a_wrong_one_refused_without_any_download(self):
        (self.directory / "fixture.bin").write_bytes(PAYLOAD)
        opener = Opener()
        self.fetch(opener)
        self.assertEqual((opener.calls, self.logs), ([], ["Verified cached fixture: fixture.bin"]))
        (self.directory / "fixture.bin").write_bytes(b"altered")
        with self.assertRaises(RuntimeError) as raised:
            self.fetch(opener)
        self.assertIn("Cached fixture failed SHA-256 verification", str(raised.exception))
        self.assertEqual(opener.calls, [])

    def test_an_invalid_name_is_refused_before_any_download(self):
        for name in ("../escape", "dir/file", ".", ".."):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self.fetch(Opener(), {"name": name, "url": URL, "sha256": DIGEST})


class PolicyTests(unittest.TestCase):
    def test_the_attempt_count_and_backoff_are_pinned_and_bounded(self):
        # Four tries with 2, 4 and 8 s between them: 14 s of waiting on top of at most four 60 s timeouts, so one
        # asset takes at most 254 s and the eleven fixtures of the longest fetch stay far inside the 30-minute job.
        self.assertEqual(assets.ATTEMPTS, 4)
        self.assertEqual(assets.BACKOFF_SECONDS, (2.0, 4.0, 8.0))
        self.assertEqual(assets.TIMEOUT_SECONDS, 60)
        self.assertEqual(len(assets.BACKOFF_SECONDS), assets.ATTEMPTS - 1)
        self.assertEqual(assets.ATTEMPTS * assets.TIMEOUT_SECONDS + sum(assets.BACKOFF_SECONDS), 254)
        self.assertEqual(assets.TRANSIENT_STATUS, frozenset({429, 500, 502, 503, 504}))

    def test_transient_and_final_errors_are_told_apart(self):
        for error in (socket.timeout(), TimeoutError(), ConnectionResetError(), ConnectionRefusedError(),
                      http.client.IncompleteRead(b""), http.client.RemoteDisconnected(), urllib.error.URLError(OSError()),
                      urllib.error.URLError(socket.gaierror()), http_error(429), http_error(500), http_error(504)):
            with self.subTest(error=type(error).__name__):
                self.assertTrue(assets.is_transient(error))
        for error in (http_error(400), http_error(403), http_error(404), http_error(410), ValueError(), KeyError(),
                      PermissionError(), urllib.error.URLError("unknown url type")):
            with self.subTest(error=type(error).__name__):
                self.assertFalse(assets.is_transient(error))

    def test_the_default_warning_goes_to_the_error_stream(self):
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(stderr):
            assets.fetch([{"name": "fixture.bin", "url": URL, "sha256": DIGEST}], directory, log=lambda _: None,
                         opener=Opener(socket.timeout("timed out")), sleep=lambda _: None)
            self.assertEqual(os.listdir(directory), ["fixture.bin"])
        self.assertIn("Retrying fixture fixture.bin", stderr.getvalue())
        self.assertIn("socket.timeout" if assets.describe(socket.timeout()) == "socket.timeout" else "TimeoutError",
                      stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
