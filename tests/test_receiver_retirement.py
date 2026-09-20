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
        self.assertIn("name: tag-offline-receiver-retirement", workflow)
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", workflow)
        self.assertIn("group: tag-offline-receiver-retirement", workflow)
        self.assertLess(workflow.index("npm run verify-retirement"), workflow.index("npm run retire-worker"))

    def test_data_retirement_is_a_confirmed_manual_workflow(self) -> None:
        root = Path(__file__).resolve().parents[1]
        data_workflow = (root / ".github/workflows/delete-hosted-receiver-data.yml").read_text(encoding="utf-8")
        ci_workflow = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")

        self.assertIn("inputs.confirmation == 'DELETE_DATA'", data_workflow)
        self.assertIn("name: tag-offline-receiver-retirement", data_workflow)
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", data_workflow)
        self.assertIn("group: tag-offline-receiver-retirement", data_workflow)
        self.assertIn("npm run retire-data", data_workflow)
        self.assertNotIn("npm run retire-data", ci_workflow)
