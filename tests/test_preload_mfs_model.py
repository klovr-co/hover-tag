from __future__ import annotations

import unittest
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


if __name__ == "__main__":
    unittest.main()
