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
                patch.object(preload_mfs_model, "configured_embedding", return_value=(provider, "m")), \
                patch.object(preload_mfs_model, "preload_default_embedding", return_value=("onnx", "m")) as preload, \
                redirect_stdout(output):
            self.assertEqual(preload_mfs_model.main(), 0)
        return output.getvalue(), preload

    def test_the_local_model_is_still_the_default(self) -> None:
        output, preload = self.run_main("onnx")
        preload.assert_called_once_with()
        self.assertIn("MFS embedding model ready", output)

    def test_a_provider_chosen_through_tag_is_read_from_its_managed_configuration(self) -> None:
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp:
            managed = Path(temp) / "shared/mfs/server.toml"
            managed.parent.mkdir(parents=True)
            managed.write_text('[embedding]\nprovider = "openai"\n', encoding="utf-8")
            with patch.dict(os.environ, {"TAG_HOME": temp}), \
                    patch.object(preload_mfs_model, "configured_embedding", return_value=("openai", "m")) as configured, \
                    redirect_stdout(StringIO()):
                self.assertEqual(preload_mfs_model.main(), 0)
            configured.assert_called_once_with(managed)

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
            self.assertEqual(preload_mfs_model.configured_embedding(), ("openai", "text-embedding-3-small"))
        loader.assert_called_once_with(None, apply_env=False)

    def test_an_unreadable_configuration_keeps_the_local_default(self) -> None:
        with patch.object(preload_mfs_model, "_server_config_loader", return_value=Mock(side_effect=ValueError("bad toml"))):
            self.assertEqual(preload_mfs_model.configured_embedding()[0], "onnx")

class BackgroundDownloadTests(unittest.TestCase):
    """`--progress` is how the installer runs the download on its own and lets Tag show it."""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.home = Path(self._temp.name)
        self.shared = self.home / "shared/mfs"

    def run_main(self, preload) -> int:
        from scripts import tag_model_download
        self.download = tag_model_download
        config = type("Config", (), {"provider": "onnx", "model": "gpahal/bge-m3-onnx-int8"})()
        with patch.dict(os.environ, {"TAG_HOME": str(self.home)}), \
                patch.object(preload_mfs_model, "configured_embedding", return_value=("onnx", "m")), \
                patch.object(preload_mfs_model, "_embedding_components", return_value=(lambda: config, None)), \
                patch.object(preload_mfs_model.tag_model_download.Recorder, "hook"), \
                patch.object(preload_mfs_model, "preload_default_embedding", side_effect=preload), \
                redirect_stdout(StringIO()):
            return preload_mfs_model.main(["--progress"])

    def test_a_finished_download_is_recorded_as_ready(self) -> None:
        self.assertEqual(self.run_main(lambda: ("onnx", "gpahal/bge-m3-onnx-int8")), 0)
        report = self.download.report(self.shared)
        self.assertEqual((report["state"], report["model"]), ("ready", "gpahal/bge-m3-onnx-int8"))

    def test_the_model_is_named_before_any_byte_arrives(self) -> None:
        seen = []
        def preload():
            seen.append(self.download.read(self.shared))
            return ("onnx", "gpahal/bge-m3-onnx-int8")
        self.run_main(preload)
        self.assertEqual((seen[0]["state"], seen[0]["model"]), ("downloading", "gpahal/bge-m3-onnx-int8"))

    def test_a_failed_download_is_recorded_with_its_reason_and_fails_the_run(self) -> None:
        def preload():
            raise OSError("no network")
        self.assertEqual(self.run_main(preload), 1)
        report = self.download.report(self.shared)
        self.assertEqual(report["state"], "failed")
        self.assertIn("no network", report["message"])

    def test_nothing_is_recorded_when_the_model_is_not_needed(self) -> None:
        from scripts import tag_model_download
        with patch.dict(os.environ, {"TAG_HOME": str(self.home), "TAG_SKIP_MFS_MODEL": "1"}), \
                redirect_stdout(StringIO()):
            self.assertEqual(preload_mfs_model.main(["--progress"]), 0)
        self.assertIsNone(tag_model_download.report(self.shared))



if __name__ == "__main__":
    unittest.main()
