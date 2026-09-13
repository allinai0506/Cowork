"""Acceptance tests for console URL query parameter deep-link navigation and task highlighting."""

import importlib.machinery
import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_console():
    path = ROOT / "console" / "herdr_factory_console.py"
    spec = importlib.util.spec_from_loader(
        "herdr_console_deep_link_test",
        importlib.machinery.SourceFileLoader("herdr_console_deep_link_test", str(path)),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestConsoleDeepLink(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = load_console().HTML

    def test_boot_parses_url_search_params(self):
        self.assertIn("URLSearchParams", self.html)
        self.assertIn("window.location.search", self.html)
        self.assertIn("workflow_id", self.html)
        self.assertIn("task_id", self.html)

    def test_render_tasks_includes_data_task_id(self):
        self.assertIn('data-task-id=', self.html)

    def test_deep_link_task_highlight_and_modal_trigger(self):
        self.assertIn("showTask", self.html)
        self.assertIn("scrollIntoView", self.html)


if __name__ == "__main__":
    unittest.main()
