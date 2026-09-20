from __future__ import annotations

import json
import unittest
from pathlib import Path


class ReceiverRetirementTests(unittest.TestCase):
    def test_retirement_config_preserves_history_and_deletes_receiver_data(self) -> None:
        root = Path(__file__).resolve().parents[1]
        config = json.loads((root / "services/receiver-retirement/wrangler.jsonc").read_text(encoding="utf-8"))

        self.assertEqual(config["name"], "tag-offline-receiver")
        self.assertNotIn("durable_objects", config)
        self.assertEqual(config["migrations"], [
            {"tag": "v1", "new_sqlite_classes": ["TagReceiver"]},
            {"tag": "v2", "deleted_classes": ["TagReceiver"]},
        ])

    def test_worker_retirement_requires_post_migration_verification(self) -> None:
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/retire-hosted-receiver.yml").read_text(encoding="utf-8")

        self.assertIn("inputs.confirmation == 'RETIRE'", workflow)
        self.assertLess(workflow.index("npm run verify-retirement"), workflow.index("npm run retire-worker"))
