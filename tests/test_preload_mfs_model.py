from __future__ import annotations

import os
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

from scripts import preload_mfs_model


class PreloadMfsModelTests(unittest.TestCase):
    def test_preloads_the_server_default_embedding_provider(self) -> None:
        provider = type("Provider", (), {"model_name": "fixture-model"})()

        config_factory = Mock(
            return_value=type(
                "Config", (), {"provider": "onnx", "model": "fixture-model"}
            )()
        )
        get_provider = Mock(return_value=provider)

        with patch.object(
            preload_mfs_model,
            "_embedding_components",
            return_value=(config_factory, get_provider),
        ):
            result = preload_mfs_model.preload_default_embedding()

        self.assertEqual(result, ("onnx", "fixture-model"))
        get_provider.assert_called_once_with("onnx", "fixture-model")

    def run_main(self, provider: str, **environment: str) -> tuple[str, Mock]:
        output = StringIO()
        with patch.dict(os.environ, environment), \
                patch.object(preload_mfs_model, "_configured_embedding", return_value=(provider, "m")), \
                patch.object(preload_mfs_model, "preload_default_embedding", return_value=("onnx", "m")) as preload, \
                redirect_stdout(output):
            self.assertEqual(preload_mfs_model.main(), 0)
        return output.getvalue(), preload

    def test_the_local_model_is_still_the_default(self) -> None:
        output, preload = self.run_main("onnx")
        preload.assert_called_once_with()
        self.assertIn("MFS embedding model ready", output)

    def test_a_hosted_provider_skips_the_download(self) -> None:
        for provider in ("openai", "gemini", "voyage", "jina", "mistral", "ollama"):
            with self.subTest(provider=provider):
                output, preload = self.run_main(provider)
                preload.assert_not_called()
                self.assertIn("skipped", output)
                self.assertIn(provider, output)

    def test_the_environment_skips_the_download_when_the_provider_is_not_known_yet(self) -> None:
        for value in ("1", "true", "YES"):
            with self.subTest(value=value):
                output, preload = self.run_main("onnx", TAG_SKIP_MFS_MODEL=value)
                preload.assert_not_called()
                self.assertIn("TAG_SKIP_MFS_MODEL", output)

    def test_other_values_of_the_skip_variable_keep_the_download(self) -> None:
        for value in ("", "0", "no"):
            with self.subTest(value=value):
                _, preload = self.run_main("onnx", TAG_SKIP_MFS_MODEL=value)
                preload.assert_called_once_with()

    def test_the_provider_comes_from_the_servers_own_configuration(self) -> None:
        config = type("Config", (), {"embedding": type("E", (), {"provider": "openai", "model": "text-embedding-3-small"})()})()
        loader = Mock(return_value=config)
        with patch.object(preload_mfs_model, "_server_config_loader", return_value=loader):
            self.assertEqual(preload_mfs_model._configured_embedding(), ("openai", "text-embedding-3-small"))
        loader.assert_called_once_with(apply_env=False)

    def test_an_unreadable_configuration_keeps_the_local_default(self) -> None:
        with patch.object(preload_mfs_model, "_server_config_loader", return_value=Mock(side_effect=ValueError("bad toml"))):
            self.assertEqual(preload_mfs_model._configured_embedding()[0], "onnx")


if __name__ == "__main__":
    unittest.main()
