"""Discovery sentinel for the separately versioned agent-task contract files."""
from __future__ import annotations

import json
from pathlib import Path
import unittest


HERE = Path(__file__).resolve().parent


class AgentTaskDiscoveryTests(unittest.TestCase):
    def test_agent_task_contract_assets_are_materialized_and_discovered(self) -> None:
        contracts = json.loads(
            (HERE / "agent_task_schemas.json").read_text(encoding="utf-8")
        )
        vectors = json.loads(
            (HERE / "agent_task_test_vectors.json").read_text(encoding="utf-8")
        )
        self.assertEqual("agent-task-contract-set/v0.1", contracts["schema"])
        self.assertEqual("agent-task-test-vectors/v0.1", vectors["schema"])
        self.assertEqual(19, len(vectors["cases"]))


if __name__ == "__main__":
    unittest.main()
