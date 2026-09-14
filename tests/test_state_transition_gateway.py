import json
import sqlite3
import time
from pathlib import Path
import pytest

from herdr.transitions import (
    TASK_TRANSITIONS,
    WORKFLOW_TRANSITIONS,
    ACTIVE_TASK_STATUSES,
    COMPLETED_TASK_STATUSES,
    TERMINAL_TASK_STATUSES,
    InvalidTransitionError,
    validate_task_transition,
    validate_workflow_transition,
)
from herdr.state_store import get_state_store, reset_state_store, SQLiteStateStore
from herdr import state_db
from herdr import kernel


@pytest.fixture
def clean_store(tmp_path, monkeypatch):
    """Provide an isolated SQLite database and reset global StateStore."""
    db_path = tmp_path / "test_state.db"
    workflows_file = tmp_path / "workflows.json"
    tasks_file = tmp_path / "tasks.json"

    workflows_file.write_text(json.dumps({"workflows": {}}), encoding="utf-8")
    tasks_file.write_text(json.dumps({"tasks": []}), encoding="utf-8")

    monkeypatch.setenv("HERDR_STATE_DB", str(db_path))
    monkeypatch.setenv("WORKFLOWS_FILE", str(workflows_file))
    monkeypatch.setenv("TASKS_FILE", str(tasks_file))

    reset_state_store()
    store = get_state_store(db_path)

    yield store, db_path, tmp_path

    reset_state_store()


class TestStateTransitionsRules:
    """Test pure functional state transition rules."""

    def test_validate_task_transitions(self):
        assert validate_task_transition("pending", "dispatched") is True
        assert validate_task_transition("dispatched", "working") is True
        assert validate_task_transition("working", "agent_done") is True
        assert validate_task_transition("agent_done", "completed") is True
        assert validate_task_transition("completed", "cleanup_ready") is True
        assert validate_task_transition("cleanup_ready", "cleaned") is True
        assert validate_task_transition("cleaned", "superseded") is True

        # Idempotent
        assert validate_task_transition("working", "working") is True

        # Illegal
        with pytest.raises(InvalidTransitionError):
            validate_task_transition("pending", "agent_done")

        with pytest.raises(InvalidTransitionError):
            validate_task_transition("completed", "working")

        with pytest.raises(InvalidTransitionError):
            validate_task_transition("superseded", "working")

    def test_validate_workflow_transitions(self):
        assert validate_workflow_transition("pending", "running") is True
        assert validate_workflow_transition("running", "paused") is True
        assert validate_workflow_transition("paused", "running") is True
        assert validate_workflow_transition("running", "completed") is True

        # Reopen
        assert validate_workflow_transition("completed", "in_progress") is True
        assert validate_workflow_transition("in_progress", "running") is True

        # Idempotent
        assert validate_workflow_transition("running", "running") is True

        # Illegal
        with pytest.raises(InvalidTransitionError):
            validate_workflow_transition("paused", "completed")

        with pytest.raises(InvalidTransitionError):
            validate_workflow_transition("pending", "non_existent_status")


class TestStateTransitionGateway:
    """Test State Transition Gateway execution, atomicity, and event capture."""

    def test_transition_task_success_and_event_recorded(self, clean_store):
        store, db_path, _ = clean_store

        # 1. Seed a task
        task = {
            "task_id": "t-gw-01",
            "workflow_id": "wf-gw-01",
            "node": "plan",
            "stage": "plan",
            "agent": "codex",
            "status": "pending",
        }
        store.save_task(task)

        # 2. Transition pending -> dispatched
        res = kernel.transition_task(
            task_id="t-gw-01",
            to_status="dispatched",
            reason="coordinator dispatched task",
            source="herdr-controller",
            metadata={"pane_id": "p-100"},
        )

        assert res["ok"] is True
        assert res["old_status"] == "pending"
        assert res["new_status"] == "dispatched"
        assert res["task_id"] == "t-gw-01"

        # Verify task in StateStore
        updated = store.get_task("t-gw-01")
        assert updated["status"] == "dispatched"
        assert updated.get("pane_id") == "p-100"

        # Verify canonical WorkflowEvent in StateStore
        events = store.list_events(task_id="t-gw-01", event_type="task_transition")
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "task_transition"
        assert ev["workflow_id"] == "wf-gw-01"
        assert ev["node_id"] == "plan"
        assert ev["agent_id"] == "codex"
        assert ev["source"] == "herdr-controller"
        assert ev["payload"]["from_status"] == "pending"
        assert ev["payload"]["to_status"] == "dispatched"
        assert ev["payload"]["reason"] == "coordinator dispatched task"
        assert ev["payload"]["pane_id"] == "p-100"

    def test_transition_task_illegal_rejected_and_no_event(self, clean_store):
        store, db_path, _ = clean_store

        task = {
            "task_id": "t-gw-02",
            "workflow_id": "wf-gw-01",
            "node": "code",
            "stage": "code",
            "agent": "claude",
            "status": "pending",
        }
        store.save_task(task)

        # Attempt illegal transition: pending -> completed
        with pytest.raises(InvalidTransitionError):
            kernel.transition_task(
                task_id="t-gw-02",
                to_status="completed",
                reason="illegal jump",
                source="rogue-caller",
            )

        # Verify task unchanged
        t = store.get_task("t-gw-02")
        assert t["status"] == "pending"

        # Verify no event created
        events = store.list_events(task_id="t-gw-02")
        assert len(events) == 0

    def test_transition_workflow_success_and_event_recorded(self, clean_store):
        store, db_path, _ = clean_store

        wf = {
            "workflow_id": "wf-gw-02",
            "title": "Gateway Test",
            "status": "running",
        }
        store.save_workflow(wf)

        # Transition running -> paused
        res = kernel.transition_workflow(
            workflow_id="wf-gw-02",
            to_status="paused",
            reason="manual pause by operator",
            source="user-cli",
            metadata={"operator": "alice"},
        )

        assert res["ok"] is True
        assert res["old_status"] == "running"
        assert res["new_status"] == "paused"

        updated = store.get_workflow("wf-gw-02")
        assert updated["status"] == "paused"

        events = store.list_events(workflow_id="wf-gw-02", event_type="workflow_transition")
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "workflow_transition"
        assert ev["source"] == "user-cli"
        assert ev["payload"]["from_status"] == "running"
        assert ev["payload"]["to_status"] == "paused"
        assert ev["payload"]["reason"] == "manual pause by operator"
        assert ev["payload"]["operator"] == "alice"

    def test_transition_workflow_illegal_rejected(self, clean_store):
        store, db_path, _ = clean_store

        wf = {
            "workflow_id": "wf-gw-03",
            "title": "Gateway Test 3",
            "status": "paused",
        }
        store.save_workflow(wf)

        with pytest.raises(InvalidTransitionError):
            kernel.transition_workflow(
                workflow_id="wf-gw-03",
                to_status="completed",
                reason="cannot complete while paused",
                source="tester",
            )

        updated = store.get_workflow("wf-gw-03")
        assert updated["status"] == "paused"

        events = store.list_events(workflow_id="wf-gw-03", event_type="workflow_transition")
        assert len(events) == 0

    def test_transition_task_nonexistent_raises_value_error(self, clean_store):
        with pytest.raises(ValueError, match="not found"):
            kernel.transition_task(
                task_id="t-non-existent",
                to_status="working",
                reason="none",
            )

    def test_transition_workflow_nonexistent_raises_value_error(self, clean_store):
        with pytest.raises(ValueError, match="not found"):
            kernel.transition_workflow(
                workflow_id="wf-non-existent",
                to_status="running",
                reason="none",
            )

    def test_transition_task_atomicity_rollback_on_failure(self, clean_store, monkeypatch):
        store, db_path, _ = clean_store

        task = {
            "task_id": "t-gw-fail",
            "workflow_id": "wf-gw-01",
            "node": "plan",
            "status": "pending",
        }
        store.save_task(task)

        # Monkeypatch record_event to raise an exception simulating write failure
        original_record_event = state_db.record_event

        def failing_record_event(*args, **kwargs):
            raise sqlite3.OperationalError("Simulated disk error during event recording")

        monkeypatch.setattr(state_db, "record_event", failing_record_event)

        with pytest.raises(sqlite3.OperationalError, match="Simulated disk error"):
            kernel.transition_task(
                task_id="t-gw-fail",
                to_status="dispatched",
                reason="should fail and rollback",
            )

        # Restore
        monkeypatch.setattr(state_db, "record_event", original_record_event)

        # Verify task is STILL pending!
        t = store.get_task("t-gw-fail")
        assert t["status"] == "pending"

        # Verify zero events created
        events = store.list_events(task_id="t-gw-fail")
        assert len(events) == 0

    def test_transition_task_force_admin_override(self, clean_store):
        store, db_path, _ = clean_store

        task = {
            "task_id": "t-gw-force",
            "workflow_id": "wf-gw-01",
            "node": "code",
            "status": "pending",
        }
        store.save_task(task)

        # Directly force to superseded (normally illegal from pending)
        res = kernel.transition_task(
            task_id="t-gw-force",
            to_status="superseded",
            reason="admin cancellation",
            source="admin_override",
            force=True,
        )

        assert res["ok"] is True
        assert res["new_status"] == "superseded"

        t = store.get_task("t-gw-force")
        assert t["status"] == "superseded"

        events = store.list_events(task_id="t-gw-force", event_type="task_transition")
        assert len(events) == 1
        assert events[0]["payload"]["forced"] is True
        assert events[0]["payload"]["to_status"] == "superseded"

    def test_kernel_primitives_emit_canonical_events(self, clean_store):
        store, db_path, tmp_path = clean_store

        # 1. Pause & Resume workflow
        wf = {
            "workflow_id": "wf-kernel-prim",
            "title": "Kernel Primitives Test",
            "status": "running",
            "config": {
                "nodes": [
                    {"id": "req", "label": "Requirements"},
                    {"id": "impl", "label": "Implementation", "depends_on": ["req"]},
                ]
            },
        }
        store.save_workflow(wf)

        res_pause = kernel.pause_workflow("wf-kernel-prim")
        assert res_pause["status"] == "paused"
        events_pause = store.list_events(workflow_id="wf-kernel-prim", event_type="workflow_transition")
        assert len(events_pause) == 1
        assert events_pause[0]["payload"]["to_status"] == "paused"

        res_resume = kernel.resume_workflow("wf-kernel-prim")
        assert res_resume["status"] == "running"
        events_resume = store.list_events(workflow_id="wf-kernel-prim", event_type="workflow_transition")
        assert len(events_resume) == 2
        assert events_resume[1]["payload"]["to_status"] == "running"

        # 2. Rollback workflow
        t_req = {
            "task_id": "t-prim-req",
            "workflow_id": "wf-kernel-prim",
            "node": "req",
            "status": "completed",
        }
        t_impl = {
            "task_id": "t-prim-impl",
            "workflow_id": "wf-kernel-prim",
            "node": "impl",
            "status": "working",
        }
        store.save_task(t_req)
        store.save_task(t_impl)

        # Roll back to "impl"
        res_rb = kernel.rollback_workflow("wf-kernel-prim", target_node_id="impl", reason="test_rollback")
        assert res_rb["ok"] is True
        assert "t-prim-impl" in res_rb["invalidated_tasks"]

        # Verify task transition event emitted for invalidated task
        t_events = store.list_events(task_id="t-prim-impl", event_type="task_transition")
        assert len(t_events) == 1
        assert t_events[0]["payload"]["to_status"] == "superseded"
        assert "rollback to impl" in t_events[0]["payload"]["reason"]

    def test_cli_set_status_emits_workflow_event(self, clean_store):
        store, db_path, tmp_path = clean_store

        task = {
            "task_id": "t-cli-01",
            "workflow_id": "wf-cli-01",
            "node": "plan",
            "stage": "plan",
            "agent": "codex",
            "status": "dispatched",
        }
        store.save_task(task)

        import os
        import subprocess
        bin_path = Path(__file__).resolve().parent.parent / "bin" / "herdr-task"
        env = os.environ.copy()
        env["HERDR_STATE_DB"] = str(db_path)
        env["TASKS_FILE"] = str(tmp_path / "tasks.json")
        env["WORKFLOWS_FILE"] = str(tmp_path / "workflows.json")
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)

        cmd = ["python3", str(bin_path), "set", "t-cli-01", "working"]
        res = subprocess.run(cmd, env=env, text=True, capture_output=True)
        assert res.returncode == 0, res.stderr

        # Verify task status in store
        t = store.get_task("t-cli-01")
        assert t["status"] == "working"

        # Verify WorkflowEvent was produced!
        events = store.list_events(task_id="t-cli-01", event_type="task_transition")
        assert len(events) == 1
        assert events[0]["payload"]["from_status"] == "dispatched"
        assert events[0]["payload"]["to_status"] == "working"
        assert events[0]["source"] == "herdr-task"

    def test_cli_supersede_emits_workflow_event(self, clean_store):
        store, db_path, tmp_path = clean_store

        task = {
            "task_id": "t-cli-02",
            "workflow_id": "wf-cli-02",
            "node": "dev",
            "stage": "dev",
            "agent": "claude",
            "status": "working",
        }
        store.save_task(task)

        import os
        import subprocess
        bin_path = Path(__file__).resolve().parent.parent / "bin" / "herdr-task"
        env = os.environ.copy()
        env["HERDR_STATE_DB"] = str(db_path)
        env["TASKS_FILE"] = str(tmp_path / "tasks.json")
        env["WORKFLOWS_FILE"] = str(tmp_path / "workflows.json")
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)

        cmd = ["python3", str(bin_path), "supersede", "t-cli-02", "--reason", "abandoned by user"]
        res = subprocess.run(cmd, env=env, text=True, capture_output=True)
        assert res.returncode == 0, res.stderr

        t = store.get_task("t-cli-02")
        assert t["status"] == "superseded"

        events = store.list_events(task_id="t-cli-02", event_type="task_transition")
        assert len(events) == 1
        assert events[0]["payload"]["from_status"] == "working"
        assert events[0]["payload"]["to_status"] == "superseded"
        assert events[0]["payload"]["reason"] == "abandoned by user"

    def test_workflow_close_and_reopen_emit_events(self, clean_store):
        store, db_path, tmp_path = clean_store

        wf = {
            "workflow_id": "wf-close-01",
            "title": "Close and Reopen Test",
            "status": "in_progress",
            "coordinator_pane_id": "pane-coord-1",
        }
        store.save_workflow(wf)

        # 1. Close workflow
        res_close = kernel.transition_workflow(
            workflow_id="wf-close-01",
            to_status="completed",
            reason="workflow_completed: delivered",
            source="herdr-task",
            metadata={"outcome": "delivered"},
        )
        assert res_close["ok"] is True
        assert res_close["new_status"] == "completed"

        events = store.list_events(workflow_id="wf-close-01", event_type="workflow_transition")
        assert len(events) == 1
        assert events[0]["payload"]["from_status"] == "in_progress"
        assert events[0]["payload"]["to_status"] == "completed"
        assert events[0]["payload"]["outcome"] == "delivered"

        # 2. Reopen workflow
        res_reopen = kernel.transition_workflow(
            workflow_id="wf-close-01",
            to_status="in_progress",
            reason="workflow_reopened",
            source="herdr-task",
            metadata={"suppress_auto_close": True},
        )
        assert res_reopen["ok"] is True
        assert res_reopen["new_status"] == "in_progress"

        events2 = store.list_events(workflow_id="wf-close-01", event_type="workflow_transition")
        assert len(events2) == 2
        assert events2[1]["payload"]["from_status"] == "completed"
        assert events2[1]["payload"]["to_status"] == "in_progress"
        assert events2[1]["payload"]["suppress_auto_close"] is True


