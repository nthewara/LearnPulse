import json
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

    def test_record_json_includes_category_from_raw_patch_status(self):
        row = {
            "id": "abc12345-0",
            "product": "aks",
            "date": "2026-07-05",
            "kind": "new-feature",
            "title": "Add widget autoscaling",
            "summary": "",
            "reasons_json": json.dumps(["new-file"]),
            "files_json": json.dumps(["articles/aks/widget-autoscaling.md"]),
            "doc_urls_json": json.dumps(["https://learn.microsoft.com/azure/aks/widget-autoscaling"]),
            "commit_url": "https://github.com/example/repo/commit/abc12345",
            "sha": "abc123456789",
            "raw_patch_summary": json.dumps({
                "files": [
                    {
                        "filename": "articles/aks/widget-autoscaling.md",
                        "status": "added",
                        "patch": "+Widget autoscaling is now in preview for AKS clusters.",
                    },
                ],
            }),
        }

        record = feeds._record_to_json(row)
        self.assertEqual(record["page_change_category"], "new-page")
        self.assertEqual(
            record["change_summary"],
            "Widget autoscaling is now in preview for AKS clusters.",
        )
        self.assertTrue(record["batch_key"].startswith("new-page:aks:widget-autoscaling"))

    def test_azure_ai_record_json_keeps_product_and_doc_url(self):
        row = {
            "id": "def45678-0",
            "product": "azure-ai-search",
            "date": "2026-07-05",
            "kind": "doc-update",
            "title": "Update indexing guide",
            "summary": "Updated indexing guidance.",
            "reasons_json": json.dumps(["doc-update"]),
            "files_json": json.dumps(["articles/search/search-how-to-index.md"]),
            "doc_urls_json": json.dumps(["https://learn.microsoft.com/azure/search/search-how-to-index"]),
            "commit_url": "https://github.com/MicrosoftDocs/azure-ai-docs/commit/def45678",
            "sha": "def456789abc",
            "raw_patch_summary": json.dumps({
                "files": [
                    {
                        "filename": "articles/search/search-how-to-index.md",
                        "status": "modified",
                        "patch": "+Use the latest Azure AI Search indexing options.",
                    },
                ],
            }),
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
            "reasons_json": json.dumps(["doc-update"]),
            "files_json": json.dumps(["articles/aks/widget-autoscaling.md"]),
            "doc_urls_json": json.dumps([]),
            "commit_url": "https://github.com/example/repo/commit/abc12345",
            "sha": "abc123456789",
            "raw_patch_summary": json.dumps({
                "files": [
                    {
                        "filename": "articles/aks/widget-autoscaling.md",
                        "status": "modified",
                        "patch": "+Widget guidance now includes rollout notes.",
                    },
                ],
            }),
            "author_login": author_login,
            "author_name": author_name,
        }


if __name__ == "__main__":
    unittest.main()
