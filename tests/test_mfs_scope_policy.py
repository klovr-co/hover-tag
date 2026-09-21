from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts import mfs_cat, mfs_ls, mfs_search


class MfsScopePolicyTests(unittest.TestCase):
    OPERATIONS = (
        ("list", mfs_ls.is_path_allowed),
        ("read", mfs_cat.is_path_allowed),
        ("search", mfs_search.is_scope_allowed),
    )

    def assert_allowed_by_every_operation(self, target: str, expected: bool) -> None:
        scopes = ["file://local/repo/allowed"]
        for operation, check in self.OPERATIONS:
            with self.subTest(operation=operation, target=target):
                self.assertEqual(expected, check(target, scopes))

    def test_allows_paths_below_configured_scope(self) -> None:
        self.assert_allowed_by_every_operation(
            "file://local/repo/allowed/document.txt", True
        )

    def test_rejects_sibling_prefix(self) -> None:
        self.assert_allowed_by_every_operation(
            "file://local/repo/allowed-copy/document.txt", False
        )

    def test_rejects_raw_parent_traversal(self) -> None:
        self.assert_allowed_by_every_operation(
            "file://local/repo/allowed/../secret", False
        )

    def test_rejects_percent_encoded_parent_traversal(self) -> None:
        self.assert_allowed_by_every_operation(
            "file://local/repo/allowed/%2e%2e/secret", False
        )

    def test_rejects_double_encoded_parent_traversal(self) -> None:
        self.assert_allowed_by_every_operation(
            "file://local/repo/allowed/%252e%252e/secret", False
        )

    def test_rejects_different_scheme(self) -> None:
        self.assert_allowed_by_every_operation(
            "slack://local/repo/allowed/document.txt", False
        )

    def test_rejects_different_authority(self) -> None:
        self.assert_allowed_by_every_operation(
            "file://remote/repo/allowed/document.txt", False
        )

    def test_all_scope_still_requires_a_valid_uri(self) -> None:
        for operation, check in self.OPERATIONS:
            with self.subTest(operation=operation):
                self.assertFalse(check("file://local/allowed/../secret", ["--all"]))

    def test_search_all_sentinel_requires_explicit_permission(self) -> None:
        self.assertTrue(mfs_search.is_scope_allowed("--all", ["--all"]))
        self.assertFalse(
            mfs_search.is_scope_allowed("--all", ["file://local/repo/allowed"])
        )

    def test_search_result_channel_label_uses_authorized_id_mapping(self) -> None:
        with patch.dict(
            "os.environ", {"OPENTAG_SLACK_CHANNEL_LABELS": '{"C2": "renamed-support"}'}, clear=False
        ):
            labels = mfs_search.channel_labels_from_env()
        self.assertEqual(
            "#renamed-support (C2)",
            mfs_search.source_channel_label(
                "slack://tag-t1/channels/old-support__C2/messages.jsonl", labels
            ),
        )
        self.assertIsNone(
            mfs_search.source_channel_label(
                "slack://tag-t1/channels/other__C20/messages.jsonl", labels
            )
        )


if __name__ == "__main__":
    unittest.main()
