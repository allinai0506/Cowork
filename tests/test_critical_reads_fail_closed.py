import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from herdr import agent_router, projects
from herdr.state_store import StateStore


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    db_file = tmp_path / "state.db"
    tasks_file = tmp_path / "tasks.json"
    workflows_file = tmp_path / "workflows.json"

    # Seed stale JSON files that would cause incorrect decisions if read
    workflows_file.write_text(
        json.dumps({
            "version": 1,
            "workflows": {
                "wf-test-01": {
                    "workflow_id": "wf-test-01",
                    "project_id": "proj-alpha",
                    "status": "running",
                    "agent_override": "stale-claude",
                }
            }
        }, ensure_ascii=False),
        encoding="utf-8"
    )

    tasks_file.write_text(
        json.dumps({
            "tasks": [
                {
                    "task_id": "task-stale-01",
                    "workflow_id": "wf-test-01",
                    "project_id": "proj-alpha",
                    "status": "working",
                    "agent": "stale-agent",
                }
            ]
        }, ensure_ascii=False),
        encoding="utf-8"
    )

    monkeypatch.setenv("HERDR_STATE_DB", str(db_file))
    monkeypatch.setenv("TASKS_FILE", str(tasks_file))
    monkeypatch.setenv("WORKFLOWS_FILE", str(workflows_file))
    monkeypatch.setattr(agent_router, "TASKS_FILE", tasks_file)
    monkeypatch.setattr(agent_router, "WORKFLOWS_FILE", workflows_file)
    monkeypatch.setattr(projects, "WORKFLOWS_FILE", workflows_file)

    return tmp_path


def test_router_workflow_record_fails_closed_on_store_error(isolated_env):
    mock_store = MagicMock(spec=StateStore)
    mock_store.get_workflow.side_effect = sqlite3.OperationalError("database disk image is malformed")

    with patch.object(agent_router, "_get_store", return_value=mock_store):
        with pytest.raises(sqlite3.OperationalError):
            agent_router.workflow_record("wf-test-01")


def test_router_active_agent_loads_fails_closed_on_store_error(isolated_env):
    mock_store = MagicMock(spec=StateStore)
    mock_store.list_tasks.side_effect = sqlite3.OperationalError("database is locked")

    with patch.object(agent_router, "_get_store", return_value=mock_store):
        with pytest.raises(sqlite3.OperationalError):
            agent_router._active_agent_loads("proj-alpha")


def test_router_clean_reservations_fails_closed_on_store_error(isolated_env):
    mock_store = MagicMock(spec=StateStore)
    mock_store.list_tasks.side_effect = sqlite3.OperationalError("disk I/O error")

    with patch.object(agent_router, "_get_store", return_value=mock_store):
        with pytest.raises(sqlite3.OperationalError):
            agent_router._clean_reservations({"reservations": {}})


def test_projects_active_workflows_fails_closed_on_store_error(isolated_env):
    mock_store = MagicMock(spec=StateStore)
    mock_store.list_workflows.side_effect = sqlite3.OperationalError("no such table: workflows")

    with patch.object(projects, "_get_store", return_value=mock_store):
        with pytest.raises(sqlite3.OperationalError):
            projects.active_workflows_for_project("proj-alpha")


def test_projects_non_terminal_workflows_fails_closed_on_store_error(isolated_env):
    mock_store = MagicMock(spec=StateStore)
    mock_store.list_workflows.side_effect = sqlite3.OperationalError("database is locked")

    with patch.object(projects, "_get_store", return_value=mock_store):
        with pytest.raises(sqlite3.OperationalError):
            projects.non_terminal_workflow_ids()


def test_projects_project_for_workflow_fails_closed_on_store_error(isolated_env):
    mock_store = MagicMock(spec=StateStore)
    mock_store.get_workflow.side_effect = sqlite3.OperationalError("disk I/O error")

    with patch.object(projects, "_get_store", return_value=mock_store):
        with pytest.raises(sqlite3.OperationalError):
            projects.project_for_workflow("wf-test-01")


def test_projects_load_workflows_fails_closed_on_store_error(isolated_env):
    mock_store = MagicMock(spec=StateStore)
    mock_store.export_workflows_json.side_effect = sqlite3.OperationalError("corrupted database")

    with patch.object(projects, "_get_store", return_value=mock_store):
        with pytest.raises(sqlite3.OperationalError):
            projects.load_workflows()


def test_controller_workflow_entry_fails_closed_on_store_error(isolated_env, monkeypatch):
    import importlib
    controller = importlib.import_module("services.herdr-controller")

    mock_store = MagicMock(spec=StateStore)
    mock_store.get_workflow.side_effect = sqlite3.OperationalError("disk I/O error")

    with patch.object(controller, "_get_store", return_value=mock_store):
        with pytest.raises(sqlite3.OperationalError):
            controller._workflow_entry("wf-test-01")


def test_controller_active_registered_workflows_fails_closed_on_store_error(isolated_env, monkeypatch):
    import importlib
    controller = importlib.import_module("services.herdr-controller")

    mock_store = MagicMock(spec=StateStore)
    mock_store.list_workflows.side_effect = sqlite3.OperationalError("disk I/O error")

    with patch.object(controller, "_get_store", return_value=mock_store):
        with pytest.raises(sqlite3.OperationalError):
            controller.active_registered_workflows()

