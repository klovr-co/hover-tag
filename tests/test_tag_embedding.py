from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from scripts import tag_embedding

URL = "http://localhost:11434/v1"


def accept(size=768):
    return lambda key, model, base_url: size


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

    def use(self, key="", model="nomic-embed-text", url=URL, size=768):
        tag_embedding.set_custom(self.shared, key, model=model, base_url=url, verify=accept(size))

    def test_nothing_is_managed_until_a_choice_is_made(self):
        self.assertIsNone(tag_embedding.managed_config(self.shared))
        self.assertEqual(tag_embedding.current_provider(self.shared), "onnx")
        self.assertEqual(tag_embedding.server_environment(self.shared), {})

    def test_custom_writes_the_model_and_measured_dimension(self):
        self.use(size=3072, model="text-embedding-3-large", url="https://api.openai.com/v1/")
        text = tag_embedding.managed_config(self.shared).read_text(encoding="utf-8")
        self.assertIn("[embedding]", text)
        self.assertIn('provider = "openai"', text)  # MFS's name for an OpenAI-style client
        self.assertIn('model = "text-embedding-3-large"', text)
        self.assertIn("dim = 3072", text)
        self.assertEqual(tag_embedding.current_provider(self.shared), "custom")

    def test_the_key_is_private_optional_and_only_reaches_mfs_through_its_environment(self):
        self.use(key="sk-test-synthetic")
        self.assertNotIn("sk-test-synthetic", tag_embedding.managed_config(self.shared).read_text(encoding="utf-8"))
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(tag_embedding.key_path(self.shared).stat().st_mode), 0o600)
        env = tag_embedding.server_environment(self.shared)
        self.assertEqual((env["OPENAI_API_KEY"], env["OPENAI_BASE_URL"]), ("sk-test-synthetic", URL))
        self.use(key="")  # a local server needs none, and the old key is dropped
        self.assertIsNone(tag_embedding.saved_key(self.shared))
        self.assertEqual(tag_embedding.server_environment(self.shared)["OPENAI_API_KEY"], tag_embedding.PLACEHOLDER_KEY)

    def test_the_address_loses_a_trailing_slash_for_mfs(self):
        self.use(url="http://localhost:11434/v1/")
        self.assertEqual(tag_embedding.server_environment(self.shared)["OPENAI_BASE_URL"], URL)
        shown = tag_embedding.choices(self.shared)[1]
        self.assertEqual((shown["model"], shown["base_url"]), ("nomic-embed-text", "http://localhost:11434/v1/"))

    def test_it_keeps_the_rest_of_an_existing_server_configuration(self):
        (self.user / "server.toml").write_text(
            '[milvus]\nuri = "http://example.invalid:19530"\n\n[embedding]\nprovider = "onnx"\nmodel = "m"\n\n[summary]\nenabled = true\n',
            encoding="utf-8")
        self.use()
        text = tag_embedding.managed_config(self.shared).read_text(encoding="utf-8")
        self.assertIn('uri = "http://example.invalid:19530"', text)
        self.assertIn("[summary]\nenabled = true", text)
        self.assertEqual(text.count("[embedding]"), 1)
        self.assertNotIn('provider = "onnx"', text)
        self.assertIn('provider = "onnx"', (self.user / "server.toml").read_text(encoding="utf-8"))  # never rewritten

    def test_switching_back_to_the_default_is_repeatable_and_keeps_other_settings_and_the_key(self):
        (self.user / "server.toml").write_text("[summary]\nenabled = true\n", encoding="utf-8")
        self.use(key="sk-test-synthetic")
        for _ in range(2):
            tag_embedding.set_local(self.shared)
        text = tag_embedding.managed_config(self.shared).read_text(encoding="utf-8")
        self.assertNotIn("[embedding]", text)
        self.assertIn("[summary]", text)
        self.assertEqual(tag_embedding.current_provider(self.shared), "onnx")
        self.assertEqual(tag_embedding.saved_key(self.shared), "sk-test-synthetic")
        self.assertEqual(tag_embedding.server_environment(self.shared), {"MFS_SERVER_CONFIG": str(tag_embedding.managed_config(self.shared))})

    def test_bad_input_is_refused_and_nothing_is_saved(self):
        for kwargs in ({"base_url": "ftp://x", "model": "m"}, {"base_url": "", "model": "m"},
                       {"base_url": "http://a b", "model": "m"}, {"base_url": URL, "model": " "},
                       {"base_url": URL, "model": "m", "key": "two words"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                tag_embedding.set_custom(self.shared, kwargs.get("key", ""), model=kwargs["model"],
                                         base_url=kwargs["base_url"], verify=accept())
        self.assertIsNone(tag_embedding.managed_config(self.shared))

    def test_an_endpoint_that_fails_the_check_changes_nothing(self):
        def reject(*_args):
            raise ValueError("localhost rejected that key.")
        with self.assertRaisesRegex(ValueError, "rejected"):
            tag_embedding.set_custom(self.shared, "k", model="m", base_url=URL, verify=reject)
        self.assertIsNone(tag_embedding.managed_config(self.shared))
        self.assertIsNone(tag_embedding.saved_key(self.shared))
        with self.assertRaisesRegex(ValueError, "vector size"):
            tag_embedding.set_custom(self.shared, "", model="m", base_url=URL)

    def test_choices_list_the_default_first(self):
        listed = tag_embedding.choices(self.shared)
        self.assertEqual([c["id"] for c in listed], ["onnx", "custom"])
        self.assertEqual([c["custom"] for c in listed], [False, True])

    def test_reindex_is_remembered_until_it_is_done(self):
        self.assertFalse(tag_embedding.reindex_pending(self.shared))
        tag_embedding.mark_reindex(self.shared)
        self.assertTrue(tag_embedding.reindex_pending(self.shared))
        tag_embedding.clear_reindex(self.shared)
        tag_embedding.clear_reindex(self.shared)
        self.assertFalse(tag_embedding.reindex_pending(self.shared))


class ProbeTests(unittest.TestCase):
    def answer(self, status=200, body=None):
        def urlopen(request, timeout=0):
            self.request = request
            if status != 200:
                raise urllib.error.HTTPError(request.full_url, status, "x", {}, io.BytesIO(b""))
            return io.BytesIO(json.dumps(body if body is not None else {"data": [{"embedding": [0.1, 0.2, 0.3]}]}).encode())
        return patch.object(tag_embedding.urllib.request, "urlopen", urlopen)

    def test_it_returns_the_vector_size_and_sends_the_key_as_a_header_only(self):
        with self.answer():
            self.assertEqual(tag_embedding.probe("sk-test-synthetic", "text-embedding-3-small", "https://api.openai.com/v1/"), 3)
        self.assertEqual(self.request.full_url, "https://api.openai.com/v1/embeddings")
        self.assertEqual(self.request.get_header("Authorization"), "Bearer sk-test-synthetic")
        self.assertNotIn(b"sk-test-synthetic", self.request.data)

    def test_no_key_sends_no_header(self):
        with self.answer():
            tag_embedding.probe("", "nomic-embed-text", URL)
        self.assertIsNone(self.request.get_header("Authorization"))

    def test_each_failure_says_what_to_do(self):
        for status, text in ((401, "localhost rejected that key"), (404, "no embedding model called m"), (429, "quota")):
            with self.subTest(status=status), self.answer(status), self.assertRaisesRegex(ValueError, text):
                tag_embedding.probe("k", "m", URL)
        with self.answer(body={"nope": 1}), self.assertRaisesRegex(ValueError, "OpenAI-compatible"):
            tag_embedding.probe("", "m", URL)
        with patch.object(tag_embedding.urllib.request, "urlopen", side_effect=OSError), \
                self.assertRaisesRegex(ValueError, "Couldn't reach localhost"):
            tag_embedding.probe("", "m", URL)


if __name__ == "__main__":
    unittest.main()
