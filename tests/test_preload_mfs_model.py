from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts import preload_mfs_model


class PreloadMfsModelTests(unittest.TestCase):
    def test_preloads_the_server_default_embedding_provider(self) -> None:
        provider = type("Provider", (), {"model_name": "fixture-model"})()

        with patch.object(
            preload_mfs_model,
            "EmbeddingConfig",
            return_value=type(
                "Config", (), {"provider": "onnx", "model": "fixture-model"}
            )(),
        ), patch.object(
            preload_mfs_model, "get_provider", return_value=provider
        ) as get_provider:
            result = preload_mfs_model.preload_default_embedding()

        self.assertEqual(result, ("onnx", "fixture-model"))
        get_provider.assert_called_once_with("onnx", "fixture-model")


if __name__ == "__main__":
    unittest.main()
