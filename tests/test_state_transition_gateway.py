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

    def test_force_with_unknown_status_rejected(self, clean_store):
        store, db_path, _ = clean_store

        task = {
            "task_id": "t-gw-force-unk",
            "workflow_id": "wf-gw-01",
            "node": "code",
            "status": "pending",
        }
        store.save_task(task)

        # Unknown task status with force=True MUST be rejected
        with pytest.raises(InvalidTransitionError, match="Invalid target task status"):
            kernel.transition_task(
                task_id="t-gw-force-unk",
                to_status="banana",
                reason="illegal unknown status",
                force=True,
            )

        wf = {
            "workflow_id": "wf-gw-force-unk",
            "title": "Force Unknown Test",
            "status": "pending",
        }
        store.save_workflow(wf)

        # Unknown workflow status with force=True MUST be rejected
        with pytest.raises(InvalidTransitionError, match="Invalid target workflow status"):
            kernel.transition_workflow(
                workflow_id="wf-gw-force-unk",
                to_status="banana",
                reason="illegal unknown status",
                force=True,
            )

    def test_force_with_illegal_edge_allowed(self, clean_store):
        store, db_path, _ = clean_store

        # 1. Task: pending -> superseded normally illegal, but allowed with force=True
        task = {
            "task_id": "t-gw-force-edge",
            "workflow_id": "wf-gw-01",
            "node": "code",
            "status": "pending",
        }
        store.save_task(task)

        res_task = kernel.transition_task(
            task_id="t-gw-force-edge",
            to_status="superseded",
            reason="admin override edge",
            force=True,
        )
        assert res_task["ok"] is True
        assert res_task["new_status"] == "superseded"
        t = store.get_task("t-gw-force-edge")
        assert t["status"] == "superseded"
        t_events = store.list_events(task_id="t-gw-force-edge")
        assert t_events[0]["payload"]["forced"] is True

        # 2. Workflow: paused -> completed normally illegal, but allowed with force=True
        wf = {
            "workflow_id": "wf-gw-force-edge",
            "title": "Force Edge Test",
            "status": "paused",
        }
        store.save_workflow(wf)

        res_wf = kernel.transition_workflow(
            workflow_id="wf-gw-force-edge",
            to_status="completed",
            reason="admin override edge",
            force=True,
        )
        assert res_wf["ok"] is True
        assert res_wf["new_status"] == "completed"
        w = store.get_workflow("wf-gw-force-edge")
        assert w["status"] == "completed"
        w_events = store.list_events(workflow_id="wf-gw-force-edge")
        assert w_events[0]["payload"]["forced"] is True

    def test_metadata_cannot_modify_protected_fields(self, clean_store):
        store, db_path, _ = clean_store

        task = {
            "task_id": "t-gw-prot",
            "workflow_id": "wf-gw-01",
            "node": "code",
            "status": "pending",
        }
        store.save_task(task)

        for protected_field in ["task_id", "workflow_id", "status", "created_at", "updated_at"]:
            with pytest.raises(ValueError, match="Cannot overwrite protected task fields via metadata"):
                kernel.transition_task(
                    task_id="t-gw-prot",
                    to_status="dispatched",
                    reason="metadata exploit attempt",
                    metadata={protected_field: "malicious_override"},
                )

        wf = {
            "workflow_id": "wf-gw-prot",
            "title": "Protected Fields Test",
            "status": "pending",
        }
        store.save_workflow(wf)

        for protected_field in ["workflow_id", "status", "created_at", "updated_at"]:
            with pytest.raises(ValueError, match="Cannot overwrite protected workflow fields via metadata"):
                kernel.transition_workflow(
                    workflow_id="wf-gw-prot",
                    to_status="running",
                    reason="metadata exploit attempt",
                    metadata={protected_field: "malicious_override"},
                )

    def test_rollback_skips_already_superseded_task(self, clean_store):
        store, db_path, _ = clean_store

        wf = {
            "workflow_id": "wf-rb-skip",
            "title": "Rollback Skip Test",
            "status": "running",
            "config": {
                "nodes": [
                    {"id": "step1", "label": "Step 1"},
                    {"id": "step2", "label": "Step 2", "depends_on": ["step1"]},
                ]
            },
        }
        store.save_workflow(wf)

        t1 = {
            "task_id": "t-rb-s1",
            "workflow_id": "wf-rb-skip",
            "node": "step2",
            "status": "working",
        }
        store.save_task(t1)

        # First rollback: supersedes task
        res1 = kernel.rollback_workflow("wf-rb-skip", target_node_id="step2", reason="rb 1")
        assert "t-rb-s1" in res1["invalidated_tasks"]
        events1 = store.list_events(task_id="t-rb-s1")
        assert len(events1) == 1

        # Second rollback: task is already superseded, must be SKIPPED!
        res2 = kernel.rollback_workflow("wf-rb-skip", target_node_id="step2", reason="rb 2")
        assert "t-rb-s1" not in res2["invalidated_tasks"]
        events2 = store.list_events(task_id="t-rb-s1")
        assert len(events2) == 1  # No duplicate event!

    def test_caller_fail_closed_when_gateway_fails(self, clean_store, monkeypatch):
        store, db_path, tmp_path = clean_store

        task = {
            "task_id": "t-fc-01",
            "workflow_id": "wf-fc-01",
            "node": "step1",
            "stage": "step1",
            "pane_id": "pane-101",
            "status": "dispatched",
        }
        store.save_task(task)

        # 1. Simulate disk / SQLite failure during record_event
        original_record_event = state_db.record_event

        def failing_record_event(*args, **kwargs):
            raise sqlite3.OperationalError("Simulated disk I/O error during event append")

        monkeypatch.setattr(state_db, "record_event", failing_record_event)

        # A. CLI set_status must exit non-zero (code 2) and NOT mutate status
        import importlib.machinery
        import importlib.util

        def _load_src_module(name, path):
            loader = importlib.machinery.SourceFileLoader(name, str(path))
            spec = importlib.util.spec_from_loader(name, loader)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod

        ht_path = Path(__file__).resolve().parent.parent / "bin" / "herdr-task"
        ht_mod = _load_src_module("herdr_task_fc_test", ht_path)
        ht_mod.TASKS_FILE = str(tmp_path / "tasks.json")
        ht_mod.WORKFLOWS_FILE = str(tmp_path / "workflows.json")

        with pytest.raises(SystemExit) as exc_info:
            ht_mod.set_status("t-fc-01", "working")
        assert exc_info.value.code == 2, f"Expected exit code 2 on failure, got {exc_info.value.code}"
        # Task in DB MUST remain dispatched (NOT changed to working!)
        t = store.get_task("t-fc-01")
        assert t["status"] == "dispatched"

        # B. Steering halt_task must return ok=False and NOT mutate status
        from herdr import steering
        # Mock adapter to succeed physically so we test Gateway failure
        from unittest.mock import patch, MagicMock
        mock_adapter = MagicMock()
        mock_adapter.protocol_level = "prototype"
        mock_adapter.name = "mock"
        mock_adapter.interrupt.return_value = {"ok": True}
        with patch.object(steering, "get_agent_adapter", return_value=mock_adapter):
            halt_res = steering.halt_task("t-fc-01", reason="test abort")
            assert halt_res["ok"] is False
            assert "transition_task_failed" in halt_res["error"]
            t_after_halt = store.get_task("t-fc-01")
            assert t_after_halt["status"] == "dispatched"

        # C. Sentinel update_statuses must NOT mutate status
        sentinel_path = Path(__file__).resolve().parent.parent / "services" / "herdr-sentinel.py"
        sentinel_mod = _load_src_module("herdr_sentinel_test", sentinel_path)
        sentinel_mod.TASKS_FILE = str(tmp_path / "tasks.json")

        sentinel_changed = sentinel_mod.update_statuses({"t-fc-01": ("failed", "sentinel timeout")})
        assert sentinel_changed is False
        t_after_sentinel = store.get_task("t-fc-01")
        assert t_after_sentinel["status"] == "dispatched"

    def test_close_workflow_fails_closed_on_pending_or_paused_without_force(self, clean_store):
        store, db_path, tmp_path = clean_store
        import importlib.machinery
        import importlib.util
        from herdr.transitions import InvalidTransitionError

        def _load_src_module(name, path):
            loader = importlib.machinery.SourceFileLoader(name, str(path))
            spec = importlib.util.spec_from_loader(name, loader)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod

        ht_path = Path(__file__).resolve().parent.parent / "bin" / "herdr-task"
        ht_mod = _load_src_module("herdr_task_close_test", ht_path)
        ht_mod.TASKS_FILE = str(tmp_path / "tasks.json")
        ht_mod.WORKFLOWS_FILE = str(tmp_path / "workflows.json")

        wf = {
            "workflow_id": "wf-close-gate",
            "project_id": "p-1",
            "status": "pending",
        }
        store.save_workflow(wf)

        # 1. Calling close_workflow without force on pending workflow MUST fail closed
        with pytest.raises(InvalidTransitionError):
            ht_mod.close_workflow("wf-close-gate", force=False)

        assert store.get_workflow("wf-close-gate")["status"] == "pending"

        # 2. Calling with force=True succeeds and marks completed
        report = ht_mod.close_workflow("wf-close-gate", force=True)
        assert report["workflow_id"] == "wf-close-gate"
        assert store.get_workflow("wf-close-gate")["status"] == "completed"

        events = store.list_events(workflow_id="wf-close-gate", event_type="workflow_transition")
        assert len(events) == 1
        assert events[0]["payload"]["forced"] is True
        assert events[0]["payload"]["to_status"] == "completed"

    def test_load_tasks_never_resurrects_deleted_tasks_from_json(self, clean_store):
        store, db_path, tmp_path = clean_store
        import importlib.machinery
        import importlib.util

        def _load_src_module(name, path):
            loader = importlib.machinery.SourceFileLoader(name, str(path))
            spec = importlib.util.spec_from_loader(name, loader)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod

        ht_path = Path(__file__).resolve().parent.parent / "bin" / "herdr-task"
        ht_mod = _load_src_module("herdr_task_resurrect_test", ht_path)
        ht_mod.TASKS_FILE = str(tmp_path / "tasks.json")
        ht_mod.WORKFLOWS_FILE = str(tmp_path / "workflows.json")

        task = {
            "task_id": "t-ghost",
            "workflow_id": "wf-ghost",
            "status": "completed",
        }
        store.save_task(task)

        # Sync to json
        ht_mod.save_tasks({"tasks": [task]})
        assert (tmp_path / "tasks.json").exists()

        # Delete from SQLite directly
        store.delete_task("t-ghost")
        assert store.get_task("t-ghost") is None

        # load_tasks() MUST NOT resurrect t-ghost back into SQLite!
        loaded = ht_mod.load_tasks()
        assert loaded["tasks"] == []
        assert store.get_task("t-ghost") is None

    def test_projection_sync_locked_reexport(self, clean_store):
        store, db_path, tmp_path = clean_store
        from herdr.state_store import sync_tasks_projection, sync_workflows_projection

        t_file = tmp_path / "tasks.json"
        w_file = tmp_path / "workflows.json"

        # Initially write items
        store.save_task({"task_id": "t-lock-1", "workflow_id": "wf-lock", "status": "dispatched"})
        store.save_workflow({"workflow_id": "wf-lock", "status": "running"})

        sync_tasks_projection(store=store, tasks_file=t_file)
        sync_workflows_projection(store=store, wf_file=w_file)

        assert t_file.exists()
        assert w_file.exists()
        lock_t = tmp_path / ".tasks.json.lock"
        lock_w = tmp_path / ".workflows.json.lock"
        assert lock_t.exists()
        assert lock_w.exists()

        with open(t_file, "r", encoding="utf-8") as f:
            t_data = json.load(f)
        assert len(t_data["tasks"]) == 1
        assert t_data["tasks"][0]["task_id"] == "t-lock-1"




