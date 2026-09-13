#!/usr/bin/env python3
"""Tests for StateStore Interface and SQLiteStateStore Implementation."""

import json
import os
import time
from pathlib import Path
import pytest

from herdr.state_store import StateStore, SQLiteStateStore, get_state_store, set_state_store, reset_state_store


@pytest.fixture
def store_env(tmp_path, monkeypatch):
    db_file = tmp_path / "state.db"
    wf_file = tmp_path / "workflows.json"
    tasks_file = tmp_path / "tasks.json"
    steering_file = tmp_path / "steering.json"
    cp_dir = tmp_path / "checkpoints"
    cp_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HERDR_STATE_DB", str(db_file))
    monkeypatch.setenv("WORKFLOWS_FILE", str(wf_file))
    monkeypatch.setenv("TASKS_FILE", str(tasks_file))
    monkeypatch.setenv("STEERING_FILE", str(steering_file))
    monkeypatch.setenv("CHECKPOINTS_DIR", str(cp_dir))

    reset_state_store()

    yield {
        "db_file": db_file,
        "wf_file": wf_file,
        "tasks_file": tasks_file,
        "steering_file": steering_file,
        "cp_dir": cp_dir,
    }

    reset_state_store()


def test_sqlite_state_store_is_instance_of_state_store(store_env):
    store = SQLiteStateStore(db_path=store_env["db_file"])
    assert isinstance(store, StateStore)


def test_workflow_crud_operations(store_env):
    store = SQLiteStateStore(db_path=store_env["db_file"])

    # 1. Save workflow
    wf = {
        "workflow_id": "wf-001",
        "title": "测试工作流 1",
        "status": "running",
        "template_name": "universal_sdlc",
        "current_stage": "dev",
        "config": {"nodes": [{"id": "dev"}]},
        "paused_nodes": ["test"],
    }
    store.save_workflow(wf)

    # 2. Get workflow
    fetched = store.get_workflow("wf-001")
    assert fetched is not None
    assert fetched["workflow_id"] == "wf-001"
    assert fetched["title"] == "测试工作流 1"
    assert fetched["status"] == "running"
    assert fetched["paused_nodes"] == ["test"]

    # 3. List workflows
    store.save_workflow({"workflow_id": "wf-002", "title": "测试工作流 2", "status": "completed"})
    all_wfs = store.list_workflows()
    assert len(all_wfs) == 2

    completed_wfs = store.list_workflows(status="completed")
    assert len(completed_wfs) == 1
    assert completed_wfs[0]["workflow_id"] == "wf-002"

    # 4. Delete workflow
    deleted = store.delete_workflow("wf-002")
    assert deleted is True
    assert store.get_workflow("wf-002") is None
    assert len(store.list_workflows()) == 1


def test_task_crud_operations(store_env):
    store = SQLiteStateStore(db_path=store_env["db_file"])

    t = {
        "task_id": "task-100",
        "workflow_id": "wf-100",
        "node": "dev",
        "stage": "dev",
        "agent": "codex",
        "status": "working",
        "pane_id": "pane-1",
        "goal": "完成模块开发",
        "custom_attr": "extra_value",
    }
    store.save_task(t)

    fetched = store.get_task("task-100")
    assert fetched is not None
    assert fetched["task_id"] == "task-100"
    assert fetched["workflow_id"] == "wf-100"
    assert fetched["agent"] == "codex"
    assert fetched["custom_attr"] == "extra_value"

    tasks = store.list_tasks(workflow_id="wf-100")
    assert len(tasks) == 1
    assert tasks[0]["task_id"] == "task-100"

    deleted = store.delete_task("task-100")
    assert deleted is True
    assert store.get_task("task-100") is None


def test_steering_operations(store_env):
    store = SQLiteStateStore(db_path=store_env["db_file"])

    steer = {
        "steer_id": "str-999",
        "task_id": "task-200",
        "workflow_id": "wf-200",
        "instruction": "请先修复测试失败",
        "operator": "commander",
        "urgent": True,
        "status": "pending",
    }
    store.save_steer(steer)

    fetched = store.get_steer("str-999")
    assert fetched is not None
    assert fetched["instruction"] == "请先修复测试失败"
    assert fetched["urgent"] is True
    assert fetched["status"] == "pending"

    # List steers
    steers = store.list_steers(task_id="task-200", status="pending")
    assert len(steers) == 1

    # Update status
    now = time.time()
    store.update_steer_status("str-999", status="dispatched", dispatched_at=now)
    fetched_after = store.get_steer("str-999")
    assert fetched_after["status"] == "dispatched"
    assert fetched_after["dispatched_at"] == now

    # Record steering history
    store.record_steering_history({
        "action": "steer_dispatched",
        "task_id": "task-200",
        "steer_id": "str-999",
        "instruction": "请先修复测试失败",
        "operator": "commander",
        "timestamp": now,
    })
    hist = store.list_steering_history(task_id="task-200")
    assert len(hist) == 1
    assert hist[0]["action"] == "steer_dispatched"


def test_checkpoint_lifecycle_via_store(store_env):
    store = SQLiteStateStore(db_path=store_env["db_file"])

    wid = "wf-cp-store"
    store.save_workflow({"workflow_id": wid, "title": "快照测试", "status": "running"})
    store.save_task({"task_id": "t-cp-1", "workflow_id": wid, "node": "dev", "status": "completed"})

    cp = store.create_checkpoint(wid, tag="v1.0")
    assert cp["ok"] is True
    cpid = cp["checkpoint_id"]

    cps = store.list_checkpoints(wid)
    assert len(cps) == 1
    assert cps[0]["checkpoint_id"] == cpid

    snap = store.get_checkpoint(wid, cpid)
    assert snap["checkpoint_id"] == cpid
    assert snap["workflow"]["title"] == "快照测试"

    # Modify and restore
    store.save_workflow({"workflow_id": wid, "title": "被污染的标题", "status": "failed"})
    res = store.restore_checkpoint(wid, cpid)
    assert res["ok"] is True
    restored_wf = store.get_workflow(wid)
    assert restored_wf["title"] == "快照测试"


def test_json_export_and_migration(store_env):
    store = SQLiteStateStore(db_path=store_env["db_file"], auto_migrate_json=False)

    wid = "wf-export"
    store.save_workflow({"workflow_id": wid, "title": "导出测试", "status": "running"})
    store.save_task({"task_id": "t-exp", "workflow_id": wid, "node": "dev", "status": "working"})
    store.save_steer({
        "steer_id": "s-exp",
        "task_id": "t-exp",
        "instruction": "测试指令",
        "status": "pending",
    })

    # Test export
    wfs_json = store.export_workflows_json()
    assert wid in wfs_json["workflows"]

    tasks_json = store.export_tasks_json()
    assert len(tasks_json["tasks"]) == 1
    assert tasks_json["tasks"][0]["task_id"] == "t-exp"

    steering_json = store.export_steering_json()
    assert "t-exp" in steering_json["steering_queues"]
    assert steering_json["steering_queues"]["t-exp"][0]["steer_id"] == "s-exp"

    # Export all
    export_dir = store_env["cp_dir"].parent / "exported_json"
    exported = store.export_all_json(export_dir)
    assert exported["ok"] is True
    assert (export_dir / "workflows.json").exists()
    assert (export_dir / "tasks.json").exists()
    assert (export_dir / "steering.json").exists()


def test_global_state_store_singleton(store_env):
    s1 = get_state_store()
    s2 = get_state_store()
    assert s1 is s2

    custom_db = store_env["cp_dir"] / "custom.db"
    s_custom = get_state_store(custom_db)
    assert s_custom.db_path == custom_db


def test_cross_module_single_source_of_truth_anti_skew(store_env):
    """Verify that kernel and steering mutations write to StateStore/SQLite as sole truth."""
    from unittest.mock import patch
    from herdr import kernel, steering

    store = get_state_store()

    wid = "wf-anti-skew-001"
    store.save_workflow({
        "workflow_id": wid,
        "title": "Anti Skew Verification",
        "status": "running",
        "config": {"nodes": [{"id": "dev"}]},
    })

    tid = "task-anti-skew-001"
    store.save_task({
        "task_id": tid,
        "workflow_id": wid,
        "node": "dev",
        "status": "working",
        "pane_id": "pane-mock-99",
    })

    # 1. Kernel pause mutation: verify SQLite reflects status immediately
    k_res = kernel.pause_workflow(wid)
    assert k_res["ok"] is True
    wf_in_sqlite = store.get_workflow(wid)
    assert wf_in_sqlite["status"] == "paused"

    # 2. Kernel resume mutation: verify SQLite reflects status immediately
    k_res2 = kernel.resume_workflow(wid)
    assert k_res2["ok"] is True
    wf_in_sqlite = store.get_workflow(wid)
    assert wf_in_sqlite["status"] == "running"

    # 3. Steering queue mutation: verify SQLite reflects steer item immediately
    st_res = steering.queue_steer(tid, "Hold execution until code review", operator="lead", execute_dispatch=False)
    assert st_res["ok"] is True
    steer_id = st_res["steer_id"]
    steer_in_sqlite = store.get_steer(steer_id)
    assert steer_in_sqlite is not None
    assert steer_in_sqlite["instruction"] == "Hold execution until code review"
    assert steer_in_sqlite["status"] == "pending"

    # 4. Steering dispatch mutation: verify SQLite reflects dispatched status and task history
    with patch("herdr.steering._send_keys", return_value=True), patch("herdr.steering._send_text", return_value=True):
        d_res = steering.dispatch_steer_now(tid, steer_id)
        assert d_res["ok"] is True
        steer_dispatched = store.get_steer(steer_id)
        assert steer_dispatched["status"] == "dispatched"

        task_in_sqlite = store.get_task(tid)
        assert task_in_sqlite["last_steered_at"] is not None
        assert len(task_in_sqlite["steering_history"]) == 1
        assert task_in_sqlite["steering_history"][0]["steer_id"] == steer_id

    # 5. Steering halt task: verify SQLite reflects interrupted status
    with patch("herdr.steering._send_keys", return_value=True):
        h_res = steering.halt_task(tid, reason="manual pause for audit", operator="lead", execute_kill=True)
        assert h_res["ok"] is True
        task_halted = store.get_task(tid)
        assert task_halted["status"] == "interrupted"
        assert task_halted["interrupt_reason"] == "manual pause for audit"

    # 6. Verify compatibility files are completely consistent with SQLite
    wfs_exported = store.export_workflows_json()
    assert wfs_exported["workflows"][wid]["status"] == "running"
    tasks_exported = store.export_tasks_json()
    assert tasks_exported["tasks"][0]["status"] == "interrupted"


def test_anti_split_brain_json_cannot_override_sqlite(store_env):
    """Verify that JSON file tampering NEVER overrides authoritative SQLite state."""
    import importlib.util
    from herdr import kernel

    store = get_state_store()

    wid = "wf-anti-sb-01"
    tid = "task-anti-sb-01"

    # 1. Authoritative SQLite state: task completed, workflow completed
    store.save_workflow({
        "workflow_id": wid,
        "title": "Anti Split Brain Workflow",
        "status": "completed",
        "config": {"nodes": [{"id": "dev"}]},
    })
    store.save_task({
        "task_id": tid,
        "workflow_id": wid,
        "node": "dev",
        "stage": "dev",
        "status": "completed",
        "stage_verdict": "pass",
    })

    # 2. Deliberately tamper with tasks.json and workflows.json on disk with stale/conflicting data
    tampered_tasks = {
        "tasks": [
            {
                "task_id": tid,
                "workflow_id": wid,
                "node": "dev",
                "stage": "dev",
                "status": "working",  # Conflicting stale status!
                "stage_verdict": None,
            }
        ]
    }
    tampered_wfs = {
        "workflows": {
            wid: {
                "workflow_id": wid,
                "title": "Tampered Title",
                "status": "running",  # Conflicting stale status!
            }
        }
    }
    store_env["tasks_file"].write_text(json.dumps(tampered_tasks), encoding="utf-8")
    store_env["wf_file"].write_text(json.dumps(tampered_wfs), encoding="utf-8")

    # 3. Access state via kernel: must strictly return SQLite authoritative state
    k_tasks = kernel.load_tasks_data()
    assert len(k_tasks["tasks"]) == 1
    assert k_tasks["tasks"][0]["status"] == "completed"

    k_wfs = kernel.load_workflows_data()
    assert k_wfs["workflows"][wid]["status"] == "completed"

    # 4. Access state via services/herdr-controller.py
    ctrl_spec = importlib.util.spec_from_file_location("controller_mod", "services/herdr-controller.py")
    ctrl = importlib.util.module_from_spec(ctrl_spec)
    ctrl_spec.loader.exec_module(ctrl)

    ctrl_tasks = ctrl.load_tasks()
    assert len(ctrl_tasks) == 1
    assert ctrl_tasks[0]["status"] == "completed"
    ctrl_t = ctrl.get_task(tid)
    assert ctrl_t is not None
    assert ctrl_t["status"] == "completed"

    # 5. Access state via bin/herdr-task
    import importlib.machinery
    loader = importlib.machinery.SourceFileLoader("herdr_task_mod", str(Path("bin/herdr-task").resolve()))
    task_spec = importlib.util.spec_from_loader("herdr_task_mod", loader)
    task_bin = importlib.util.module_from_spec(task_spec)
    loader.exec_module(task_bin)

    bin_tasks = task_bin.load_tasks()
    assert bin_tasks.get("tasks", [])[0]["status"] == "completed"

    # 6. Verify SQLite itself remained 100% untainted
    fresh_task = store.get_task(tid)
    assert fresh_task["status"] == "completed"
    assert fresh_task["stage_verdict"] == "pass"

    fresh_wf = store.get_workflow(wid)
    assert fresh_wf["status"] == "completed"

    # 7. Verify one-way cold boot import for completely new/unseeded items
    new_tid = "task-cold-boot-99"
    tampered_tasks["tasks"].append({
        "task_id": new_tid,
        "workflow_id": wid,
        "node": "test",
        "stage": "test",
        "status": "pending",
    })
    store_env["tasks_file"].write_text(json.dumps(tampered_tasks), encoding="utf-8")

    # Before load, it is not in SQLite
    assert store.get_task(new_tid) is None

    # After load, the missing task is imported into SQLite
    kernel.load_tasks_data()
    imported = store.get_task(new_tid)
    assert imported is not None
    assert imported["status"] == "pending"

    # Now tamper with this new task in JSON to try overriding it
    tampered_tasks["tasks"][1]["status"] = "failed"
    store_env["tasks_file"].write_text(json.dumps(tampered_tasks), encoding="utf-8")

    kernel.load_tasks_data()
    # Must remain "pending" in SQLite, proving subsequent disk sync does NOT overwrite
    assert store.get_task(new_tid)["status"] == "pending"


def test_herdr_task_cli_writes_directly_to_sqlite(store_env):
    """Verify that herdr-task CLI writes directly to SQLite as authoritative storage."""
    import subprocess

    store = get_state_store()

    wid = "wf-cli-01"
    tid = "task-cli-01"

    store.save_workflow({
        "workflow_id": wid,
        "title": "CLI Direct Write Workflow",
        "status": "running",
        "config": {"nodes": [{"id": "dev"}]},
    })
    store.save_task({
        "task_id": tid,
        "workflow_id": wid,
        "node": "dev",
        "stage": "dev",
        "status": "dispatched",
    })

    env = os.environ.copy()
    env["HERDR_STATE_DB"] = str(store_env["db_file"])
    env["TASKS_FILE"] = str(store_env["tasks_file"])
    env["WORKFLOWS_FILE"] = str(store_env["wf_file"])

    # 1. Transition task: dispatched -> working
    proc = subprocess.run(
        ["python3", "bin/herdr-task", "set", tid, "working"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"STDOUT: {proc.stdout}, STDERR: {proc.stderr}"
    assert f"{tid}: dispatched -> working" in proc.stdout

    # Assert SQLite database directly reflects 'working'
    t_after = store.get_task(tid)
    assert t_after is not None
    assert t_after["status"] == "working"
    assert t_after["started_at"] is not None
    assert len(t_after.get("status_history", [])) >= 1
    assert t_after["status_history"][-1]["to"] == "working"

    # 2. Transition task: working -> agent_done -> completed with verdict
    proc_done = subprocess.run(
        ["python3", "bin/herdr-task", "set", tid, "agent_done"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc_done.returncode == 0, f"STDOUT: {proc_done.stdout}, STDERR: {proc_done.stderr}"

    proc2 = subprocess.run(
        ["python3", "bin/herdr-task", "set", tid, "completed", "--verdict", "pass"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc2.returncode == 0, f"STDOUT: {proc2.stdout}, STDERR: {proc2.stderr}"

    # Assert SQLite database directly reflects 'completed' and 'stage_verdict'
    t_completed = store.get_task(tid)
    assert t_completed is not None
    assert t_completed["status"] == "completed"
    assert t_completed["stage_verdict"] == "pass"


