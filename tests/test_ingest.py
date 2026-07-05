import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

import ingest  # noqa: E402


class FakeResponse:
    def __init__(self, body, headers=None):
        self._body = json.dumps(body).encode("utf-8")
        self.headers = headers or {"X-RateLimit-Remaining": "10"}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class RequestTests(unittest.TestCase):
    def setUp(self):
        ingest._rate_limited = False

    def test_request_uses_github_token_as_bearer_auth(self):
        captured = {}
        token = "example-token"

        def fake_urlopen(req, timeout):
            captured["authorization"] = req.get_header("Authorization")
            captured["timeout"] = timeout
            return FakeResponse({"ok": True})

        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": token}, clear=True), \
             mock.patch("urllib.request.urlopen", fake_urlopen):
            body, headers = ingest._request("https://api.github.test/repos/example")

        self.assertEqual(body, {"ok": True})
        self.assertEqual(headers["X-RateLimit-Remaining"], "10")
        self.assertEqual(captured["authorization"], f"Bearer {token}")
        self.assertEqual(captured["timeout"], 60)

    def test_request_omits_auth_header_without_token(self):
        captured = {}

        def fake_urlopen(req, timeout):
            captured["authorization"] = req.get_header("Authorization")
            return FakeResponse([])

        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("urllib.request.urlopen", fake_urlopen):
            body, _ = ingest._request("https://api.github.test/repos/example")

        self.assertEqual(body, [])
        self.assertIsNone(captured["authorization"])


if __name__ == "__main__":
    unittest.main()
