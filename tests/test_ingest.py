import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

import ingest  # noqa: E402
import db  # noqa: E402


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


class IngestAuthorTests(unittest.TestCase):
    PRODUCT = {
        "id": "aks",
        "repo": "example/docs",
        "path": "articles/aks",
    }

    def test_ingest_stores_author_login_name_fallback_and_empty_values(self):
        commits = [
            self._list_commit("ccc33333cccc3333", author_name=None),
            self._list_commit("bbb22222bbbb2222", author_name="List Git Author"),
            self._list_commit("aaa11111aaaa1111", author_name="List Octo"),
        ]
        details = {
            "aaa11111aaaa1111": self._detail(
                author={"login": "octocat"},
                author_name="Octo Cat",
            ),
            "bbb22222bbbb2222": self._detail(
                author=None,
                author_name="Git Author",
            ),
            "ccc33333cccc3333": self._detail(author=None, author_name=None),
        }

        conn = db.connect(":memory:")
        with mock.patch.object(ingest, "list_commits", return_value=commits), \
             mock.patch.object(
                 ingest,
                 "fetch_commit_detail",
                 side_effect=lambda repo, sha: details[sha],
             ):
            counters = ingest.ingest_product(
                conn,
                self.PRODUCT,
                since_days=1,
                max_commits=None,
            )

        self.assertEqual(counters["new"], 3)
        rows = conn.execute(
            "SELECT sha, author_login, author_name FROM change_records ORDER BY sha"
        ).fetchall()
        self.assertEqual(
            [(r["sha"], r["author_login"], r["author_name"]) for r in rows],
            [
                ("aaa11111aaaa1111", "octocat", "Octo Cat"),
                ("bbb22222bbbb2222", None, "Git Author"),
                ("ccc33333cccc3333", None, None),
            ],
        )

    def _list_commit(self, sha, author_name):
        author = {"date": "2026-07-05T00:00:00Z"}
        if author_name is not None:
            author["name"] = author_name
        return {
            "sha": sha,
            "author": None,
            "commit": {
                "message": f"Update docs {sha}",
                "author": author,
                "committer": {"date": "2026-07-05T00:00:00Z"},
            },
        }

    def _detail(self, author, author_name):
        git_author = {}
        if author_name is not None:
            git_author["name"] = author_name
        return {
            "author": author,
            "commit": {"author": git_author},
            "files": [
                {
                    "filename": "articles/aks/widget-autoscaling.md",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 0,
                    "patch": "+Updated guidance.",
                },
            ],
        }


if __name__ == "__main__":
    unittest.main()
