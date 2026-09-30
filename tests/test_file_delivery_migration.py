from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import tag_config


class FileDeliveryMigrationTests(unittest.TestCase):
    def test_old_installation_migrates_once_and_preserves_operator_settings(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            config = home / "config/settings.json"
            tag_config.save_config(config, {"OPENTAG_BOT_NAME": "Custom"})
            self.assertTrue(tag_config.migrate_file_delivery(home, config))
            self.assertEqual(tag_config.read_config(config), {
                "OPENTAG_BOT_NAME": "Custom", "OPENTAG_FILE_DELIVERY": "local+slack",
            })
            tag_config.update_config(config, {"OPENTAG_FILE_DELIVERY": "local"})
            self.assertFalse(tag_config.migrate_file_delivery(home, config))
            self.assertEqual(tag_config.read_config(config)["OPENTAG_FILE_DELIVERY"], "local")

    def test_explicit_local_choice_survives_first_migration(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            config = home / "config/settings.json"
            tag_config.save_config(config, {"OPENTAG_FILE_DELIVERY": "local"})
            self.assertTrue(tag_config.migrate_file_delivery(home, config))
            self.assertEqual(tag_config.read_config(config)["OPENTAG_FILE_DELIVERY"], "local")

    def test_interrupted_checkpoint_is_retryable(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            config = home / "config/settings.json"
            marker = home / "state/migrations/file-delivery-v1.json"
            tag_config.save_config(config, {"OPENTAG_BOT_NAME": "Custom"})
            save = tag_config.save_config

            def interrupt(path, values):
                if path == marker:
                    raise OSError("interrupted")
                save(path, values)

            with patch.object(tag_config, "save_config", side_effect=interrupt):
                with self.assertRaises(OSError):
                    tag_config.migrate_file_delivery(home, config)
            self.assertFalse(marker.exists())
            self.assertEqual(tag_config.read_config(config)["OPENTAG_FILE_DELIVERY"], "local+slack")
            self.assertTrue(tag_config.migrate_file_delivery(home, config))
            self.assertFalse(tag_config.migrate_file_delivery(home, config))

    def test_failed_settings_write_never_records_completion(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            config = home / "config/settings.json"
            tag_config.save_config(config, {"OPENTAG_BOT_NAME": "Custom"})
            with patch.object(tag_config, "update_config", side_effect=OSError("read only")):
                with self.assertRaises(OSError):
                    tag_config.migrate_file_delivery(home, config)
            self.assertFalse((home / "state/migrations/file-delivery-v1.json").exists())
            self.assertTrue(tag_config.migrate_file_delivery(home, config))
