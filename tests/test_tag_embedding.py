from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import tag_embedding


class EmbeddingConfigTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.shared = self.root / "shared/mfs"
        self.shared.mkdir(parents=True)
        self.user = self.root / "user-mfs"
        self.user.mkdir()
        patcher = patch.dict(os.environ, {"MFS_HOME": str(self.user)}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("MFS_SERVER_CONFIG", None)

    def test_nothing_is_managed_until_the_provider_is_chosen(self):
        self.assertIsNone(tag_embedding.managed_config(self.shared))
        self.assertIsNone(tag_embedding.saved_key(self.shared))

    def test_openai_writes_the_provider_and_dimension_and_stores_the_key_privately(self):
        tag_embedding.set_openai(self.shared, "sk-test-synthetic")
        text = tag_embedding.managed_config(self.shared).read_text(encoding="utf-8")
        self.assertIn('[embedding]', text)
        self.assertIn('provider = "openai"', text)
        self.assertIn('model = "text-embedding-3-small"', text)
        self.assertIn("dim = 1536", text)
        self.assertNotIn("sk-test-synthetic", text)
        self.assertEqual(tag_embedding.saved_key(self.shared), "sk-test-synthetic")
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(tag_embedding.key_path(self.shared).stat().st_mode), 0o600)

    def test_it_keeps_the_rest_of_an_existing_server_configuration(self):
        (self.user / "server.toml").write_text(
            '[milvus]\nuri = "http://example.invalid:19530"\n\n[embedding]\nprovider = "onnx"\nmodel = "m"\n\n[summary]\nenabled = true\n',
            encoding="utf-8")
        tag_embedding.set_openai(self.shared, "sk-test-synthetic")
        text = tag_embedding.managed_config(self.shared).read_text(encoding="utf-8")
        self.assertIn('uri = "http://example.invalid:19530"', text)
        self.assertIn("[summary]\nenabled = true", text)
        self.assertEqual(text.count("[embedding]"), 1)
        self.assertNotIn('provider = "onnx"', text)
        # The person's own file is never rewritten.
        self.assertIn('provider = "onnx"', (self.user / "server.toml").read_text(encoding="utf-8"))

    def test_switching_back_to_local_is_repeatable_and_keeps_other_settings(self):
        (self.user / "server.toml").write_text('[summary]\nenabled = true\n', encoding="utf-8")
        tag_embedding.set_openai(self.shared, "sk-test-synthetic")
        for _ in range(2):
            tag_embedding.set_local(self.shared)
        text = tag_embedding.managed_config(self.shared).read_text(encoding="utf-8")
        self.assertNotIn("[embedding]", text)
        self.assertIn("[summary]", text)

    def test_a_malformed_key_is_refused_and_nothing_is_saved(self):
        for key in ("", "  ", "has space", "line\nbreak"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                tag_embedding.set_openai(self.shared, key)
        self.assertIsNone(tag_embedding.managed_config(self.shared))

    def test_a_key_that_fails_the_check_changes_nothing(self):
        def reject(_key):
            raise ValueError("OpenAI rejected that key.")
        with self.assertRaisesRegex(ValueError, "rejected"):
            tag_embedding.set_openai(self.shared, "sk-test-synthetic", verify=reject)
        self.assertIsNone(tag_embedding.managed_config(self.shared))
        self.assertIsNone(tag_embedding.saved_key(self.shared))

    def test_server_environment_points_at_the_managed_file_and_passes_the_key_by_environment(self):
        self.assertEqual(tag_embedding.server_environment(self.shared), {})
        tag_embedding.set_openai(self.shared, "sk-test-synthetic")
        env = tag_embedding.server_environment(self.shared)
        self.assertEqual(env["MFS_SERVER_CONFIG"], str(tag_embedding.managed_config(self.shared)))
        self.assertEqual(env["OPENAI_API_KEY"], "sk-test-synthetic")
        tag_embedding.set_local(self.shared)
        self.assertNotIn("OPENAI_API_KEY", tag_embedding.server_environment(self.shared))

    def test_reindex_is_remembered_until_it_is_done(self):
        self.assertFalse(tag_embedding.reindex_pending(self.shared))
        tag_embedding.mark_reindex(self.shared)
        self.assertTrue(tag_embedding.reindex_pending(self.shared))
        tag_embedding.clear_reindex(self.shared)
        tag_embedding.clear_reindex(self.shared)
        self.assertFalse(tag_embedding.reindex_pending(self.shared))

    def test_the_hosted_provider_needs_its_key(self):
        tag_embedding.set_openai(self.shared, "sk-test-synthetic")
        tag_embedding.key_path(self.shared).unlink()
        self.assertEqual(tag_embedding.problem(self.shared), "The OpenAI key is missing. Save it again.")


if __name__ == "__main__":
    unittest.main()
