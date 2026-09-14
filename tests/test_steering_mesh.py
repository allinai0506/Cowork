"""Tests for the Worker Intervention & Steering Mesh (herdr/steering.py)."""

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from herdr import steering


@pytest.fixture
def steering_env(tmp_path, monkeypatch):
    tasks_file = tmp_path / "tasks.json"
    steering_file = tmp_path / "steering.json"
    workflows_file = tmp_path / "workflows.json"

    monkeypatch.setenv("TASKS_FILE", str(tasks_file))
    monkeypatch.setenv("STEERING_FILE", str(steering_file))
    monkeypatch.setenv("WORKFLOWS_FILE", str(workflows_file))

    tasks_file.write_text(json.dumps({"tasks": []}), encoding="utf-8")
    steering_file.write_text(json.dumps({"steering_queues": {}, "history": []}), encoding="utf-8")
    workflows_file.write_text(json.dumps({"workflows": {}}), encoding="utf-8")

    return {
        "tasks_file": tasks_file,
        "steering_file": steering_file,
        "workflows_file": workflows_file,
    }


def _seed_task(env, task_id, status="working", pane_id="pane-101", workflow_id="wf-test"):
    from herdr.state_store import get_state_store
    t_data = json.loads(env["tasks_file"].read_text(encoding="utf-8"))
    task = {
        "task_id": task_id,
        "workflow_id": workflow_id,
        "status": status,
        "pane_id": pane_id,
        "node": "dev",
        "agent": "codex",
        "status_history": [{"from": None, "to": status, "timestamp": time.time()}],
    }
    t_data["tasks"].append(task)
    env["tasks_file"].write_text(json.dumps(t_data), encoding="utf-8")
    store = get_state_store(db_path=env["tasks_file"].parent / "state.db")
    store.save_task(task)
    return task


def test_format_steer_prompt():
    prompt = steering.format_steer_prompt("Please stick to standard library", operator="commander")
    assert "【总指挥实时插话纠偏指令 - STEERING INSTRUCTION】" in prompt
    assert "commander" in prompt
    assert "Please stick to standard library" in prompt


def test_queue_steer_normal(steering_env):
    task_id = "task-001"
    _seed_task(steering_env, task_id, status="working")

    res = steering.queue_steer(task_id, "Avoid external dependencies", operator="human", urgent=False)
    assert res["ok"] is True
    assert res["status"] == "queued"
    assert res["urgent"] is False
    steer_id = res["steer_id"]

    # Verify steering.json state
    s_data = steering.load_steering_data()
    q = s_data["steering_queues"].get(task_id, [])
    assert len(q) == 1
    assert q[0]["steer_id"] == steer_id
    assert q[0]["instruction"] == "Avoid external dependencies"
    assert q[0]["status"] == "pending"


def test_urgent_steer_immediate_dispatch(steering_env):
    task_id = "task-002"
    _seed_task(steering_env, task_id, status="working", pane_id="pane-999")

    mock_run = MagicMock()
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = ""
    mock_run.return_value.stderr = ""

    with patch("subprocess.run", mock_run):
        res = steering.queue_steer(task_id, "Stop! Fix syntax error immediately", operator="lead", urgent=True)

    assert res["ok"] is True
    assert res["status"] == "dispatched"
    assert res["urgent"] is True

    # Verify subprocess calls: ctrl-c, send-text, enter
    calls = mock_run.call_args_list
    assert len(calls) >= 2

    # Check first call sends ctrl-c
    first_cmd = calls[0][0][0]
    assert "herdr" in first_cmd and "send-keys" in first_cmd and "ctrl-c" in first_cmd

    # Check subsequent call sends text
    text_calls = [c for c in calls if "send-text" in c[0][0]]
    assert len(text_calls) >= 1
    cmd = text_calls[0][0][0]
    assert "Stop! Fix syntax error immediately" in cmd[4]

    # Verify steering.json state
    s_data = steering.load_steering_data()
    q = s_data["steering_queues"].get(task_id, [])
    assert len(q) == 1
    assert q[0]["status"] == "dispatched"
    assert q[0]["dispatched_at"] is not None

    # Verify tasks.json updated with steering history
    t_data = json.loads(steering_env["tasks_file"].read_text(encoding="utf-8"))
    t = next(x for x in t_data["tasks"] if x["task_id"] == task_id)
    assert len(t.get("steering_history", [])) == 1
    assert t["steering_history"][0]["instruction"] == "Stop! Fix syntax error immediately"


def test_halt_task_lifecycle(steering_env):
    task_id = "task-003"
    _seed_task(steering_env, task_id, status="working", pane_id="pane-888")

    mock_run = MagicMock()
    mock_run.return_value.returncode = 0

    with patch("subprocess.run", mock_run):
        res = steering.halt_task(task_id, reason="Severe logic flaw detected", operator="lead")

    assert res["ok"] is True
    assert res["status"] == "interrupted"

    # Verify ctrl-c sent
    assert any("ctrl-c" in c[0][0] for c in mock_run.call_args_list)

    # Verify task status in tasks.json
    t_data = json.loads(steering_env["tasks_file"].read_text(encoding="utf-8"))
    t = next(x for x in t_data["tasks"] if x["task_id"] == task_id)
    assert t["status"] == "interrupted"
    assert t["interrupt_reason"] == "Severe logic flaw detected"
    assert t["status_history"][-1]["to"] == "interrupted"


def test_drain_pending_steer(steering_env):
    task_id = "task-004"
    _seed_task(steering_env, task_id, status="working", pane_id="pane-777")

    # Queue a normal steer
    res_q = steering.queue_steer(task_id, "Use atomic write for json files", urgent=False)
    steer_id = res_q["steer_id"]

    mock_run = MagicMock()
    mock_run.return_value.returncode = 0

    with patch("subprocess.run", mock_run):
        res_drain = steering.dispatch_pending_steer(task_id)

    assert res_drain is not None
    assert res_drain["ok"] is True
    assert res_drain["steer_id"] == steer_id

    # Check text sent
    text_calls = [c for c in mock_run.call_args_list if "send-text" in c[0][0]]
    assert len(text_calls) >= 1
    cmd = text_calls[0][0][0]
    assert "Use atomic write for json files" in cmd[4]

    # Check queue status updated
    s_data = steering.load_steering_data()
    q = s_data["steering_queues"][task_id]
    assert q[0]["status"] == "dispatched"

    # Draining again returns None
    assert steering.dispatch_pending_steer(task_id) is None


def test_steer_nonexistent_or_inactive_task(steering_env):
    with pytest.raises(ValueError, match="Task 'unknown' not found"):
        steering.queue_steer("unknown", "Do something")

    _seed_task(steering_env, "done-task", status="completed")
    with pytest.raises(ValueError, match="inactive status"):
        steering.queue_steer("done-task", "Do something")

    with pytest.raises(ValueError, match="Task 'unknown' not found"):
        steering.halt_task("unknown")
