"""Tests for project deregistration / unregistering in Herdr Factory."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from herdr import projects as herdr_projects


class TestProjectUnregisterLogic(unittest.TestCase):
    def test_unregister_raises_if_project_not_found(self):
        with patch.object(herdr_projects, "load_projects", return_value={"projects": {}}):
            with self.assertRaises(ValueError):
                herdr_projects.unregister_project("/path/to/nonexistent")

    def test_unregister_rejects_active_workflows_without_force(self):
        fake_record = {
            "project_id": "p-123",
            "project_name": "test-p",
            "project_root": "/path/to/p",
            "workspace_id": "wX",
        }
        with patch.object(herdr_projects, "load_projects", return_value={"projects": {"/path/to/p": fake_record}}), \
             patch.object(herdr_projects, "project_by_root", return_value=fake_record), \
             patch.object(herdr_projects, "active_workflows_for_project", return_value=[{"workflow_id": "wf-1", "status": "working"}]):
            with self.assertRaises(RuntimeError) as ctx:
                herdr_projects.unregister_project("/path/to/p", force=False)
            self.assertIn("工作流", str(ctx.exception))

    def test_unregister_succeeds_with_force_even_if_active(self):
        fake_record = {
            "project_id": "p-123",
            "project_name": "test-p",
            "project_root": "/path/to/p",
            "workspace_id": "wX",
        }
        projects_data = {"projects": {"/path/to/p": fake_record}}
        with patch.object(herdr_projects, "load_projects", return_value=projects_data), \
             patch.object(herdr_projects, "project_by_root", return_value=fake_record), \
             patch.object(herdr_projects, "active_workflows_for_project", return_value=[{"workflow_id": "wf-1"}]), \
             patch.object(herdr_projects, "save_projects") as mock_save:
            record = herdr_projects.unregister_project("/path/to/p", force=True)
            self.assertEqual(record["project_id"], "p-123")
            mock_save.assert_called_once()
            self.assertNotIn("/path/to/p", projects_data["projects"])

    def test_unregister_closes_workspace_when_requested(self):
        fake_record = {
            "project_id": "p-123",
            "project_name": "test-p",
            "project_root": "/path/to/p",
            "workspace_id": "wX",
        }
        projects_data = {"projects": {"/path/to/p": fake_record}}
        with patch.object(herdr_projects, "load_projects", return_value=projects_data), \
             patch.object(herdr_projects, "project_by_root", return_value=fake_record), \
             patch.object(herdr_projects, "active_workflows_for_project", return_value=[]), \
             patch.object(herdr_projects, "_workspace_alive", return_value=True), \
             patch.object(herdr_projects, "_run") as mock_run, \
             patch.object(herdr_projects, "save_projects"):
            herdr_projects.unregister_project("/path/to/p", close_workspace=True)
            mock_run.assert_called_with(["herdr", "workspace", "close", "wX"], check=False)


class TestConsoleUnregisterEndpointAndUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import importlib.util
        path = Path(__file__).resolve().parent.parent / "console" / "herdr_factory_console.py"
        spec = importlib.util.spec_from_file_location("console_mod_unreg", str(path))
        cls.console = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.console)

    def test_frontend_has_unregister_button_and_modal(self):
        html = getattr(self.console, "HTML_TEMPLATE", "")
        self.assertIn("showUnregisterProjectModal()", html)
        self.assertIn("注销项目", html)
        self.assertIn("submitUnregisterProject()", html)


if __name__ == "__main__":
    unittest.main()
