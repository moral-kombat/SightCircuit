import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import observation_page as page  # noqa: E402

VALID = {
    "target": "rug",
    "notes": "",
    "visible_in_headset": "yes",
    "labels_matched": "partly",
    "passthrough_normal": "yes",
}
FORM = {"Content-Type": "application/x-www-form-urlencoded"}


class ObservationPageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.run_dir = Path(temporary.name) / "run"
        self.observations = self.run_dir / "observations.json"
        self.server = page.make_server(("127.0.0.1", 0), self.run_dir, "run-1", "tok")
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(conn.close)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response.status, response.read().decode("utf-8")

    def submit(self, **overrides):
        fields = dict(VALID, **overrides)
        return self.request("POST", "/tok/submit", body=urlencode(fields), headers=FORM)

    def test_wrong_token_is_not_served(self):
        self.assertEqual(self.request("GET", "/wrong/")[0], 404)
        status, _ = self.request("POST", "/wrong/submit", body=urlencode(VALID), headers=FORM)
        self.assertEqual(status, 404)
        self.assertFalse(self.observations.exists())

    def test_page_shows_run_and_saved_count(self):
        status, body = self.request("GET", "/tok/")
        self.assertEqual(status, 200)
        self.assertIn("Run run-1", body)
        self.assertIn("0 saved submission(s)", body)

    def test_valid_submission_is_appended_with_run_id(self):
        status, _ = self.submit()
        self.assertEqual(status, 303)
        data = json.loads(self.observations.read_text(encoding="utf-8"))
        self.assertEqual(data["run_id"], "run-1")
        self.assertEqual(len(data["submissions"]), 1)
        self.assertEqual(data["submissions"][0]["answers"], VALID)
        self.assertIn("submitted_at", data["submissions"][0])
        self.assertIn("1 saved submission(s)", self.request("GET", "/tok/")[1])

    def test_second_submission_is_appended(self):
        self.submit()
        self.submit(labels_matched="no")
        submissions = json.loads(self.observations.read_text(encoding="utf-8"))["submissions"]
        self.assertEqual([item["answers"]["labels_matched"] for item in submissions], ["partly", "no"])

    def test_invalid_choice_is_rejected_without_writing(self):
        status, _ = self.submit(visible_in_headset="maybe")
        self.assertEqual(status, 400)
        self.assertFalse(self.observations.exists())

    def test_missing_choice_is_rejected_without_writing(self):
        fields = {key: value for key, value in VALID.items() if key != "labels_matched"}
        status, _ = self.request("POST", "/tok/submit", body=urlencode(fields), headers=FORM)
        self.assertEqual(status, 400)
        self.assertFalse(self.observations.exists())

    def test_unknown_field_is_rejected_without_writing(self):
        status, _ = self.submit(extra="x")
        self.assertEqual(status, 400)
        self.assertFalse(self.observations.exists())

    def test_oversize_body_is_rejected_without_reading(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(conn.close)
        conn.putrequest("POST", "/tok/submit")
        conn.putheader("Content-Type", "application/x-www-form-urlencoded")
        conn.putheader("Content-Length", str(page.MAX_BODY + 1))
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 413)
        self.assertFalse(self.observations.exists())

    def test_overlong_notes_are_rejected(self):
        answers, error = page.validate(dict({key: [value] for key, value in VALID.items()},
                                            notes=["x" * (page.MAX_TEXT + 1)]))
        self.assertIsNone(answers)
        self.assertIn("notes", error)


if __name__ == "__main__":
    unittest.main()
