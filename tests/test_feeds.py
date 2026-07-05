import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

import feeds  # noqa: E402


class FeedCategoryTests(unittest.TestCase):
    def test_added_markdown_page_is_new_page(self):
        self.assertEqual(
            feeds.page_change_category([
                {"filename": "articles/aks/widget-autoscaling.md", "status": "added"},
            ], ["new-file"]),
            "new-page",
        )

    def test_modified_markdown_page_is_existing_page(self):
        self.assertEqual(
            feeds.page_change_category([
                {"filename": "articles/aks/scale-cluster.md", "status": "modified"},
            ], ["doc-update"]),
            "existing-page",
        )

    def test_mixed_added_and_modified_favors_new_page(self):
        self.assertEqual(
            feeds.page_change_category([
                {"filename": "articles/aks/scale-cluster.md", "status": "modified"},
                {"filename": "articles/aks/widget-autoscaling.md", "status": "added"},
            ], ["new-file", "doc-update"]),
            "new-page",
        )

    def test_legacy_files_use_new_file_reason_for_backward_compatibility(self):
        self.assertEqual(
            feeds.page_change_category([
                "articles/aks/widget-autoscaling.md",
            ], ["new-file"]),
            "new-page",
        )

    def test_record_json_uses_persisted_display_fields(self):
        row = {
            "id": "abc12345-0",
            "product": "aks",
            "date": "2026-07-05",
            "kind": "new-feature",
            "title": "Add widget autoscaling",
            "summary": "LLM summary not used by dashboard.",
            "change_summary": "Widget autoscaling is now in preview for AKS clusters.",
            "page_change_category": "new-page",
            "batch_key": "new-page:aks:widget-autoscaling-is-now-in-preview",
            "reasons": ["new-file"],
            "files": ["articles/aks/widget-autoscaling.md"],
            "doc_urls": ["https://learn.microsoft.com/azure/aks/widget-autoscaling"],
            "commit_url": "https://github.com/example/repo/commit/abc12345",
            "sha": "abc123456789",
        }

        record = feeds._record_to_json(row)
        self.assertEqual(record["page_change_category"], "new-page")
        self.assertEqual(
            record["change_summary"],
            "Widget autoscaling is now in preview for AKS clusters.",
        )
        self.assertEqual(record["summary"], record["change_summary"])
        self.assertEqual(record["batch_key"], "new-page:aks:widget-autoscaling-is-now-in-preview")

    def test_azure_ai_record_json_keeps_product_and_doc_url(self):
        row = {
            "id": "def45678-0",
            "product": "azure-ai-search",
            "date": "2026-07-05",
            "kind": "doc-update",
            "title": "Update indexing guide",
            "summary": "Updated indexing guidance.",
            "change_summary": "Updated indexing guidance.",
            "page_change_category": "existing-page",
            "batch_key": "existing-page:azure-ai-search:updated-indexing-guidance",
            "reasons": ["doc-update"],
            "files": ["articles/search/search-how-to-index.md"],
            "doc_urls": ["https://learn.microsoft.com/azure/search/search-how-to-index"],
            "commit_url": "https://github.com/MicrosoftDocs/azure-ai-docs/commit/def45678",
            "sha": "def456789abc",
        }

        record = feeds._record_to_json(row)
        self.assertEqual(record["product"], "azure-ai-search")
        self.assertEqual(record["page_change_category"], "existing-page")
        self.assertEqual(record["doc_urls"], [
            "https://learn.microsoft.com/azure/search/search-how-to-index",
        ])
        self.assertTrue(record["batch_key"].startswith("existing-page:azure-ai-search:"))

    def test_record_json_emits_author_fields_without_affecting_batch_key(self):
        row = self._author_row(author_login="octocat", author_name="Octo Cat")
        no_author_row = self._author_row(author_login="", author_name=None)

        record = feeds._record_to_json(row)
        no_author_record = feeds._record_to_json(no_author_row)

        self.assertEqual(record["author"], "octocat")
        self.assertEqual(record["author_name"], "Octo Cat")
        self.assertNotIn("author", no_author_record)
        self.assertNotIn("author_name", no_author_record)
        self.assertEqual(record["batch_key"], no_author_record["batch_key"])

    def _author_row(self, author_login, author_name):
        return {
            "id": "abc12345-0",
            "product": "aks",
            "date": "2026-07-05",
            "kind": "doc-update",
            "title": "Update widget guidance",
            "summary": "Updated widget guidance.",
            "change_summary": "Updated widget guidance.",
            "page_change_category": "existing-page",
            "batch_key": "existing-page:aks:updated-widget-guidance",
            "reasons": ["doc-update"],
            "files": ["articles/aks/widget-autoscaling.md"],
            "doc_urls": [],
            "commit_url": "https://github.com/example/repo/commit/abc12345",
            "sha": "abc123456789",
            "author_login": author_login,
            "author_name": author_name,
        }


if __name__ == "__main__":
    unittest.main()
