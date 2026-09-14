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


def test_stale_json_never_resurrects_deleted_or_missing_workflow(isolated_env):
    """Test A: Verify stale JSON never resurrects deleted or missing workflows into SQLite."""
    import importlib
    controller = importlib.import_module("services.herdr-controller")
    from herdr.state_store import get_state_store
    store = get_state_store()

    # The isolated_env fixture creates workflows.json with "wf-test-01".
    # During the initial DB init in this test, wf-test-01 was bootstrapped.
    # Now we explicitly delete it from the authoritative SQLite database.
    store.delete_workflow("wf-test-01")
    assert store.get_workflow("wf-test-01") is None

    # Verify stale workflows.json still contains wf-test-01 on disk
    wf_file = Path(isolated_env) / "workflows.json"
    disk_data = json.loads(wf_file.read_text(encoding="utf-8"))
    assert "wf-test-01" in disk_data.get("workflows", {})

    # Read through all critical control paths
    assert projects.project_for_workflow("wf-test-01") is None
    assert len(projects.active_workflows_for_project("proj-alpha")) == 0
    assert "wf-test-01" not in projects.non_terminal_workflow_ids()
    assert agent_router.workflow_record("wf-test-01") == {}
    assert controller._workflow_entry("wf-test-01") == {}
    assert "wf-test-01" not in controller.active_registered_workflows()

    # Crucial assertion: SQLite was NEVER resurrected from stale JSON
    assert store.get_workflow("wf-test-01") is None


def test_choose_agent_fails_closed_on_unknown_workflow(isolated_env):
    """Test B: Verify choose_agent raises RuntimeError when workflow does not exist in StateStore."""
    # 1. Unknown workflow_id must fail-closed with RuntimeError
    with pytest.raises(RuntimeError) as exc_info:
        agent_router.choose_agent("wf-non-existent-999", stage="implementation", task_type="dev")
    assert "Workflow not found in authoritative StateStore: wf-non-existent-999" in str(exc_info.value)

    # 2. Standalone task without workflow_id is permitted and returns fallback / requested agent
    res = agent_router.choose_agent(None, stage="implementation", task_type="dev", requested="auto")
    assert res == "opencode"

    res_req = agent_router.choose_agent(None, stage="implementation", task_type="dev", requested="claude")
    assert res_req == "claude"


def test_controller_active_registered_workflows_ignores_projects_json_ghosts(isolated_env, monkeypatch):
    """Test C: Verify controller.active_registered_workflows() 100% ignores projects.json ghost workflows."""
    import importlib
    controller = importlib.import_module("services.herdr-controller")

    # Inject ghost workflow into projects.json
    ghost_dir = isolated_env / ".herdr-controller"
    ghost_dir.mkdir(parents=True, exist_ok=True)
    ghost_proj_file = ghost_dir / "projects.json"
    ghost_proj_file.write_text(json.dumps({
        "projects": {
            "p1": {
                "project_id": "proj-ghost",
                "workflow_id": "ghost-wf-999"
            }
        }
    }), encoding="utf-8")
    monkeypatch.setenv("HOME", str(isolated_env))

    # Controller's active workflows must NOT contain ghost-wf-999
    active = controller.active_registered_workflows()
    assert "ghost-wf-999" not in active


def test_one_time_bootstrap_migration_then_strict_isolation(tmp_path, monkeypatch):
    """Test D: Verify legacy JSON is bootstrapped once on empty DB, but subsequent JSON edits are ignored."""
    from herdr.state_store import get_state_store
    new_db = tmp_path / "bootstrap_test" / "state.db"
    new_dir = new_db.parent
    new_dir.mkdir(parents=True, exist_ok=True)

    legacy_wf = new_dir / "workflows.json"
    legacy_tasks = new_dir / "tasks.json"

    wid = "wf-legacy-boot"
    tid = "task-legacy-boot"

    legacy_wf.write_text(json.dumps({
        "version": 1,
        "workflows": {
            wid: {
                "workflow_id": wid,
                "project_id": "proj-boot",
                "status": "running",
            }
        }
    }), encoding="utf-8")

    legacy_tasks.write_text(json.dumps({
        "tasks": [
            {
                "task_id": tid,
                "workflow_id": wid,
                "status": "pending",
            }
        ]
    }), encoding="utf-8")

    # Initialize store for the first time
    store = get_state_store(db_path=new_db)

    # Bootstrapped into SQLite
    assert store.get_workflow(wid) is not None
    assert store.get_workflow(wid)["status"] == "running"
    assert store.get_task(tid) is not None

    # Now add a new task and workflow to the JSON files after initialization
    legacy_tasks.write_text(json.dumps({
        "tasks": [
            {"task_id": "task-after-boot", "workflow_id": wid, "status": "pending"}
        ]
    }), encoding="utf-8")

    # The store was already initialized (v1_migration_done); subsequent reads MUST NOT import it
    assert store.get_task("task-after-boot") is None

