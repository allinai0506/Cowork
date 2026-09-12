"""Tests for agent router load calculations and console preflight filtering."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from herdr.agent_router import _active_agent_loads


class TestAgentRouterLoads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="herdr-agent-router-")
        self.task_file = Path(self.tmp.name) / "tasks.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_active_agent_loads_filters_out_completed_tasks(self):
        data = {
            "tasks": [
                {"task_id": "t1", "project_id": "proj1", "agent": "codex", "status": "completed"},
                {"task_id": "t2", "project_id": "proj1", "agent": "codex", "status": "integrated"},
                {"task_id": "t3", "project_id": "proj1", "agent": "codex", "status": "working"},
                {"task_id": "t4", "project_id": "proj1", "agent": "opencode", "status": "dispatched"},
                {"task_id": "t5", "project_id": "proj1", "agent": "claude", "status": "cleaned"},
            ]
        }
        self.task_file.write_text(json.dumps(data), encoding="utf-8")

        with patch("herdr.agent_router.TASKS_FILE", self.task_file):
            loads = _active_agent_loads("proj1")

        self.assertEqual(loads.get("codex"), 1)
        self.assertEqual(loads.get("opencode"), 1)
        self.assertNotIn("claude", loads)


if __name__ == "__main__":
    unittest.main()
