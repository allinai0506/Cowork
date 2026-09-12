"""Regression tests for Herdr Worker agent startup commands."""

import importlib.machinery
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent


def load_worker():
    path = ROOT / "services" / "herdr-worker.py"
    spec = importlib.util.spec_from_loader(
        "herdr_worker_test",
        importlib.machinery.SourceFileLoader("herdr_worker_test", str(path)),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestStartAgent(unittest.TestCase):
    def test_agy_start_skips_permission_prompts(self):
        worker = load_worker()
        response = {"result": {"agent": {"agent": "agy"}}}

        with patch.object(worker, "run_json", return_value=response) as run_json:
            agent = worker.start_agent("urgent-fix", "agy", "w1:p2", retries=1)

        self.assertEqual(agent, response["result"]["agent"])
        self.assertEqual(
            run_json.call_args.args[0],
            [
                "herdr",
                "agent",
                "start",
                worker.unique_agent_name("urgent-fix", "w1:p2"),
                "--kind",
                "agy",
                "--pane",
                "w1:p2",
                "--timeout",
                "120000",
                "--",
                "--dangerously-skip-permissions",
            ],
        )


if __name__ == "__main__":
    unittest.main()
