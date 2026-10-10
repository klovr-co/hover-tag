from __future__ import annotations

import json
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
        self.assertIsNone(tag_embedding.saved_key(self.shared, "openai"))

    def test_openai_writes_the_provider_and_dimension_and_stores_the_key_privately(self):
        tag_embedding.set_hosted(self.shared, "openai", "sk-test-synthetic")
        text = tag_embedding.managed_config(self.shared).read_text(encoding="utf-8")
        self.assertIn('[embedding]', text)
        self.assertIn('provider = "openai"', text)
        self.assertIn('model = "text-embedding-3-small"', text)
        self.assertIn("dim = 1536", text)
        self.assertNotIn("sk-test-synthetic", text)
        self.assertEqual(tag_embedding.saved_key(self.shared, "openai"), "sk-test-synthetic")
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(tag_embedding.key_path(self.shared, "openai").stat().st_mode), 0o600)

    def test_it_keeps_the_rest_of_an_existing_server_configuration(self):
        (self.user / "server.toml").write_text(
            '[milvus]\nuri = "http://example.invalid:19530"\n\n[embedding]\nprovider = "onnx"\nmodel = "m"\n\n[summary]\nenabled = true\n',
            encoding="utf-8")
        tag_embedding.set_hosted(self.shared, "openai", "sk-test-synthetic")
        text = tag_embedding.managed_config(self.shared).read_text(encoding="utf-8")
        self.assertIn('uri = "http://example.invalid:19530"', text)
        self.assertIn("[summary]\nenabled = true", text)
        self.assertEqual(text.count("[embedding]"), 1)
        self.assertNotIn('provider = "onnx"', text)
        # The person's own file is never rewritten.
        self.assertIn('provider = "onnx"', (self.user / "server.toml").read_text(encoding="utf-8"))

    def test_switching_back_to_local_is_repeatable_and_keeps_other_settings(self):
        (self.user / "server.toml").write_text('[summary]\nenabled = true\n', encoding="utf-8")
        tag_embedding.set_hosted(self.shared, "openai", "sk-test-synthetic")
        for _ in range(2):
            tag_embedding.set_local(self.shared)
        text = tag_embedding.managed_config(self.shared).read_text(encoding="utf-8")
        self.assertNotIn("[embedding]", text)
        self.assertIn("[summary]", text)

    def test_a_malformed_key_is_refused_and_nothing_is_saved(self):
        for key in ("", "  ", "has space", "line\nbreak"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                tag_embedding.set_hosted(self.shared, "openai", key)
        self.assertIsNone(tag_embedding.managed_config(self.shared))

    def test_a_key_that_fails_the_check_changes_nothing(self):
        def reject(*_args):
            raise ValueError("OpenAI rejected that key.")
        with self.assertRaisesRegex(ValueError, "rejected"):
            tag_embedding.set_hosted(self.shared, "openai", "sk-test-synthetic", verify=reject)
        self.assertIsNone(tag_embedding.managed_config(self.shared))
        self.assertIsNone(tag_embedding.saved_key(self.shared, "openai"))

    def test_server_environment_points_at_the_managed_file_and_passes_the_key_by_environment(self):
        self.assertEqual(tag_embedding.server_environment(self.shared), {})
        tag_embedding.set_hosted(self.shared, "openai", "sk-test-synthetic")
        env = tag_embedding.server_environment(self.shared)
        self.assertEqual(env["MFS_SERVER_CONFIG"], str(tag_embedding.managed_config(self.shared)))
        self.assertEqual(env["OPENAI_API_KEY"], "sk-test-synthetic")
        tag_embedding.set_local(self.shared)
        self.assertNotIn("OPENAI_API_KEY", tag_embedding.server_environment(self.shared))

    def test_jina_is_configured_with_its_own_key_variable_and_dimension(self):
        tag_embedding.set_hosted(self.shared, "jina", "jina_synthetic")
        text = tag_embedding.managed_config(self.shared).read_text(encoding="utf-8")
        self.assertIn('provider = "jina"', text)
        self.assertIn("dim = 2048", text)
        env = tag_embedding.server_environment(self.shared)
        self.assertEqual(env["JINA_API_KEY"], "jina_synthetic")
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertEqual(tag_embedding.current_provider(self.shared), "jina")

    def test_each_provider_keeps_its_own_saved_key_when_switching(self):
        tag_embedding.set_hosted(self.shared, "openai", "sk-test-synthetic")
        tag_embedding.set_hosted(self.shared, "jina", "jina_synthetic")
        tag_embedding.set_hosted(self.shared, "openai", "sk-test-synthetic")
        self.assertEqual(tag_embedding.saved_key(self.shared, "jina"), "jina_synthetic")
        self.assertEqual(tag_embedding.server_environment(self.shared)["OPENAI_API_KEY"], "sk-test-synthetic")
        self.assertNotIn("JINA_API_KEY", tag_embedding.server_environment(self.shared))

    def test_an_unknown_provider_is_refused(self):
        with self.assertRaisesRegex(ValueError, "Choose one of"):
            tag_embedding.set_hosted(self.shared, "nonesuch", "k")

    def test_choices_list_the_local_model_first_and_which_keys_are_saved(self):
        tag_embedding.set_hosted(self.shared, "jina", "jina_synthetic")
        listed = tag_embedding.choices(self.shared)
        self.assertEqual([c["id"] for c in listed], ["onnx", "openai", "jina", "custom"])
        self.assertEqual({c["id"]: c["key_saved"] for c in listed}, {"onnx": False, "openai": False, "jina": True, "custom": False})
        self.assertFalse(listed[0]["hosted"])

    def test_the_model_can_be_chosen_and_sets_the_vector_size(self):
        tag_embedding.set_hosted(self.shared, "openai", "sk-test-synthetic", model="text-embedding-3-large")
        text = tag_embedding.managed_config(self.shared).read_text(encoding="utf-8")
        self.assertIn('model = "text-embedding-3-large"', text)
        self.assertIn("dim = 3072", text)
        self.assertEqual(tag_embedding.choice(self.shared)["model"], "text-embedding-3-large")
        with self.assertRaisesRegex(ValueError, "offers: text-embedding-3-small, text-embedding-3-large"):
            tag_embedding.set_hosted(self.shared, "openai", "sk-test-synthetic", model="nonesuch")

    def test_custom_endpoint_uses_the_probed_vector_size_and_needs_no_key(self):
        seen = []

        def verify(provider, key, model, base_url):
            seen.append((provider, key, model, base_url))
            return 768

        tag_embedding.set_hosted(self.shared, "custom", "", model="nomic-embed-text",
                                 base_url="http://localhost:11434/v1/", verify=verify)
        self.assertEqual(seen, [("custom", "", "nomic-embed-text", "http://localhost:11434/v1/")])
        text = tag_embedding.managed_config(self.shared).read_text(encoding="utf-8")
        self.assertIn('provider = "openai"', text)
        self.assertIn('model = "nomic-embed-text"', text)
        self.assertIn("dim = 768", text)
        env = tag_embedding.server_environment(self.shared)
        self.assertEqual(env["OPENAI_BASE_URL"], "http://localhost:11434/v1")
        self.assertEqual(env["OPENAI_API_KEY"], tag_embedding.PLACEHOLDER_KEY)
        self.assertIsNone(tag_embedding.problem(self.shared))
        self.assertEqual(tag_embedding.current_provider(self.shared), "custom")
        shown = [c for c in tag_embedding.choices(self.shared) if c["id"] == "custom"][0]
        self.assertEqual((shown["model"], shown["base_url"]), ("nomic-embed-text", "http://localhost:11434/v1/"))

    def test_custom_endpoint_is_checked_before_anything_is_saved(self):
        for kwargs in ({"model": "m", "base_url": "ftp://x"}, {"model": "m", "base_url": ""},
                       {"model": "", "base_url": "http://localhost:1/v1"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                tag_embedding.set_hosted(self.shared, "custom", "", **kwargs)
        # No probe answer and no known model means the vector size is unknown.
        with self.assertRaisesRegex(ValueError, "vector size"):
            tag_embedding.set_hosted(self.shared, "custom", "", model="m", base_url="http://localhost:1/v1")
        self.assertIsNone(tag_embedding.managed_config(self.shared))

    def test_openai_pins_its_own_address_so_a_stray_environment_cannot_redirect_memory(self):
        tag_embedding.set_hosted(self.shared, "openai", "sk-test-synthetic")
        self.assertEqual(tag_embedding.server_environment(self.shared)["OPENAI_BASE_URL"], "https://api.openai.com/v1")

    def test_switching_back_to_default_forgets_the_choice_but_keeps_keys(self):
        tag_embedding.set_hosted(self.shared, "openai", "sk-test-synthetic")
        tag_embedding.set_local(self.shared)
        self.assertEqual(tag_embedding.current_provider(self.shared), "onnx")
        self.assertEqual(tag_embedding.saved_key(self.shared, "openai"), "sk-test-synthetic")

    def test_reindex_is_remembered_until_it_is_done(self):
        self.assertFalse(tag_embedding.reindex_pending(self.shared))
        tag_embedding.mark_reindex(self.shared)
        self.assertTrue(tag_embedding.reindex_pending(self.shared))
        tag_embedding.clear_reindex(self.shared)
        tag_embedding.clear_reindex(self.shared)
        self.assertFalse(tag_embedding.reindex_pending(self.shared))

    def test_the_hosted_provider_needs_its_key(self):
        tag_embedding.set_hosted(self.shared, "openai", "sk-test-synthetic")
        tag_embedding.key_path(self.shared, "openai").unlink()
        self.assertEqual(tag_embedding.problem(self.shared), "The OpenAI key is missing. Save it again.")


class ProbeTests(unittest.TestCase):
    def answer(self, status=200, body=None):
        import io
        import urllib.error

        def urlopen(request, timeout=0):
            self.request = request
            if status != 200:
                raise urllib.error.HTTPError(request.full_url, status, "x", {}, io.BytesIO(b""))
            return io.BytesIO(json.dumps(body if body is not None else {"data": [{"embedding": [0.1, 0.2, 0.3]}]}).encode())

        return patch.object(tag_embedding.urllib.request, "urlopen", urlopen)

    def test_it_returns_the_vector_size_and_sends_the_key_as_a_header(self):
        with self.answer():
            self.assertEqual(tag_embedding.probe("openai", "sk-test-synthetic", "text-embedding-3-small"), 3)
        self.assertEqual(self.request.full_url, "https://api.openai.com/v1/embeddings")
        self.assertEqual(self.request.get_header("Authorization"), "Bearer sk-test-synthetic")
        self.assertNotIn(b"sk-test-synthetic", self.request.data)

    def test_a_custom_address_is_used_and_no_key_sends_no_header(self):
        with self.answer():
            tag_embedding.probe("custom", "", "nomic-embed-text", "http://localhost:11434/v1/")
        self.assertEqual(self.request.full_url, "http://localhost:11434/v1/embeddings")
        self.assertIsNone(self.request.get_header("Authorization"))

    def test_each_failure_says_what_to_do(self):
        for status, text in ((401, "rejected that key"), (404, "no embedding model called m"), (429, "quota")):
            with self.subTest(status=status), self.answer(status), self.assertRaisesRegex(ValueError, text):
                tag_embedding.probe("openai", "k", "m")
        with self.answer(body={"nope": 1}), self.assertRaisesRegex(ValueError, "OpenAI-compatible"):
            tag_embedding.probe("custom", "", "m", "http://localhost:1/v1")
        with patch.object(tag_embedding.urllib.request, "urlopen", side_effect=OSError), \
                self.assertRaisesRegex(ValueError, "Couldn't reach localhost"):
            tag_embedding.probe("custom", "", "m", "http://localhost:1/v1")


if __name__ == "__main__":
    unittest.main()
