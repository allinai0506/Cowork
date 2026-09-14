"""Worker Intervention & Steering Mesh (herdr/steering.py).

NOTE ON PROTOCOL STATUS:
This module currently operates as a TTY-level steering prototype via AgentAdapter,
NOT a universal agent steering protocol. Heterogeneous agents (Claude, Codex,
OpenCode, Qoder, Agy, Pi) differ substantially in their handling of:
- Ctrl-C interrupt signals
- Interactive prompt injection & stdin consumption
- Multi-turn session state preservation
- Contextual session resumption

All agent-specific runtime differences and capabilities are formalized through
herdr.agent_adapter.AgentAdapter.
"""

import json
import os
import subprocess
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import state_db
from .agent_adapter import (
    AgentAdapter,
    AgentCapability,
    get_agent_adapter,
    list_agent_adapters,
)
from .state_store import get_state_store, StateStore

ACTIVE_STATUSES = {"dispatched", "working", "rework", "blocked", "paused", "interrupted"}


def get_tasks_file() -> Path:
    p = os.environ.get("TASKS_FILE")
    if p:
        return Path(p)
    return Path.home() / ".herdr-controller" / "tasks.json"


def get_steering_file() -> Path:
    p = os.environ.get("STEERING_FILE")
    if p:
        return Path(p)
    return Path.home() / ".herdr-controller" / "steering.json"


def _atomic_write_json(file_path: Path, data: Any) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(file_path.parent),
        prefix=f".tmp_{file_path.name}_",
    )
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, file_path)


def _sync_tasks_file(store: StateStore) -> None:
    tasks_file = get_tasks_file()
    if tasks_file.parent.exists():
        _atomic_write_json(tasks_file, store.export_tasks_json())


def _sync_steering_file(store: StateStore) -> None:
    st_file = get_steering_file()
    if st_file.parent.exists():
        _atomic_write_json(st_file, store.export_steering_json())


def load_tasks_data() -> Dict[str, Any]:
    store = get_state_store()
    return store.export_tasks_json()


def save_tasks_data(data: Dict[str, Any]) -> None:
    store = get_state_store()
    for t in data.get("tasks", []):
        if t.get("task_id") and t.get("workflow_id"):
            store.save_task(t)
    tasks_file = get_tasks_file()
    if tasks_file.parent.exists():
        _atomic_write_json(tasks_file, data)


def load_steering_data() -> Dict[str, Any]:
    store = get_state_store()
    return store.export_steering_json()


def save_steering_data(data: Dict[str, Any]) -> None:
    store = get_state_store()
    for tid, q in data.get("steering_queues", {}).items():
        for item in q:
            item.setdefault("task_id", tid)
            store.save_steer(item)
    for h in data.get("history", []):
        store.record_steering_history(h)
    st_file = get_steering_file()
    if st_file.parent.exists():
        _atomic_write_json(st_file, data)



def format_steer_prompt(instruction: str, operator: str = "human") -> str:
    """Format structured high-priority intervention prompt for Agent."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"【总指挥实时插话纠偏指令 - STEERING INSTRUCTION】\n"
        f"发起人：{operator} | 时间：{now_str}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "总指挥向你发送了高优先级干预指令，请立即优先吸收并按此调整后续动作：\n"
        f"> {instruction}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    )


def _send_keys(pane_id: str, key: str) -> bool:
    """Send keystroke (e.g. enter, ctrl-c) to Herdr pane."""
    try:
        r = subprocess.run(
            ["herdr", "pane", "send-keys", pane_id, key],
            text=True,
            capture_output=True,
            timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def _send_text(pane_id: str, text: str) -> bool:
    """Send text prompt to Herdr pane."""
    try:
        r = subprocess.run(
            ["herdr", "pane", "send-text", pane_id, text],
            text=True,
            capture_output=True,
            timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def queue_steer(
    task_id: str,
    instruction: str,
    operator: str = "human",
    urgent: bool = False,
    execute_dispatch: bool = True,
) -> Dict[str, Any]:
    """Queue a steering instruction for a task. If urgent, dispatch immediately."""
    instruction = (instruction or "").strip()
    if not instruction:
        raise ValueError("Instruction cannot be empty")

    tasks_data = load_tasks_data()
    task = next((t for t in tasks_data.get("tasks", []) if t.get("task_id") == task_id), None)
    if not task:
        raise ValueError(f"Task '{task_id}' not found")

    status = task.get("status")
    if status not in ACTIVE_STATUSES:
        raise ValueError(f"Task '{task_id}' is in inactive status '{status}' and cannot receive steering instructions")

    steer_id = f"str-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    steer_item = {
        "steer_id": steer_id,
        "task_id": task_id,
        "instruction": instruction,
        "operator": operator,
        "urgent": urgent,
        "created_at": time.time(),
        "status": "pending",
        "dispatched_at": None,
    }

    s_data = load_steering_data()
    q = s_data.setdefault("steering_queues", {}).setdefault(task_id, [])
    q.append(steer_item)
    save_steering_data(s_data)

    if urgent and execute_dispatch:
        return dispatch_steer_now(task_id, steer_id)

    return {
        "ok": True,
        "steer_id": steer_id,
        "task_id": task_id,
        "urgent": urgent,
        "status": "queued",
    }


def get_task_adapter(task_id: str) -> AgentAdapter:
    """Resolve the AgentAdapter for a given task, falling back to tty_prototype."""
    tasks_data = load_tasks_data()
    task = next((t for t in tasks_data.get("tasks", []) if t.get("task_id") == task_id), None)
    agent_name = task.get("agent") if task else None
    return get_agent_adapter(agent_name)


def dispatch_steer_now(task_id: str, steer_id: str) -> Dict[str, Any]:
    """Immediately dispatch a specific steer item via the task's AgentAdapter (TTY prototype)."""
    s_data = load_steering_data()
    q = s_data.get("steering_queues", {}).get(task_id, [])
    item = next((x for x in q if x.get("steer_id") == steer_id), None)
    if not item:
        raise ValueError(f"Steer item '{steer_id}' not found for task '{task_id}'")

    tasks_data = load_tasks_data()
    task = next((t for t in tasks_data.get("tasks", []) if t.get("task_id") == task_id), None)
    pane_id = task.get("pane_id") if task else None
    agent_name = task.get("agent") if task else None
    adapter = get_agent_adapter(agent_name)

    # 1. Dispatch urgent intervention via AgentAdapter
    steer_result = {"ok": True, "interrupted": False, "injected": False}
    if pane_id:
        steer_result = adapter.steer_urgent(
            pane_id,
            instruction=item["instruction"],
            operator=item.get("operator", "human"),
            wait_after_interrupt=0.1,
        )

    # 2. Update steer item status & protocol metadata
    now = time.time()
    item["status"] = "dispatched"
    item["dispatched_at"] = now
    item["protocol"] = adapter.protocol_level
    item["adapter"] = adapter.name

    s_data.setdefault("history", []).append({
        "action": "steer_dispatched",
        "steer_id": steer_id,
        "task_id": task_id,
        "instruction": item["instruction"],
        "operator": item.get("operator"),
        "urgent": item.get("urgent", False),
        "protocol": adapter.protocol_level,
        "adapter": adapter.name,
        "timestamp": now,
    })
    save_steering_data(s_data)

    # 3. Record on task entity in tasks.json
    if task:
        task["last_steered_at"] = now
        steering_history = list(task.get("steering_history") or [])
        steering_history.append({
            "steer_id": steer_id,
            "instruction": item["instruction"],
            "operator": item.get("operator"),
            "urgent": item.get("urgent", False),
            "protocol": adapter.protocol_level,
            "adapter": adapter.name,
            "dispatched_at": now,
        })
        task["steering_history"] = steering_history
        save_tasks_data(tasks_data)

    return {
        "ok": True,  # "ok" = steer was recorded as dispatched, regardless of TTY delivery
        "steer_id": steer_id,
        "task_id": task_id,
        "urgent": item.get("urgent", False),
        "status": "dispatched",
        "protocol": adapter.protocol_level,
        "adapter": adapter.name,
        "pane_delivery_ok": steer_result.get("ok", True),  # TTY-level send result for observability
        "dispatched_at": item["dispatched_at"],
    }


def dispatch_pending_steer(task_id: str, steer_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Dispatch the next pending steer item via task's AgentAdapter (soft non-interrupting)."""
    s_data = load_steering_data()
    q = s_data.get("steering_queues", {}).get(task_id, [])
    if not q:
        return None

    target_item = None
    if steer_id:
        target_item = next((x for x in q if x.get("steer_id") == steer_id and x.get("status") == "pending"), None)
    else:
        target_item = next((x for x in q if x.get("status") == "pending"), None)

    if not target_item:
        return None

    tasks_data = load_tasks_data()
    task = next((t for t in tasks_data.get("tasks", []) if t.get("task_id") == task_id), None)
    pane_id = task.get("pane_id") if task else None
    agent_name = task.get("agent") if task else None
    adapter = get_agent_adapter(agent_name)

    # Inject without interrupt for smooth steering
    steer_result = {"ok": True, "interrupted": False, "injected": False}
    if pane_id:
        steer_result = adapter.steer_soft(
            pane_id,
            instruction=target_item["instruction"],
            operator=target_item.get("operator", "human"),
        )

    now = time.time()
    target_item["status"] = "dispatched"
    target_item["dispatched_at"] = now
    target_item["protocol"] = adapter.protocol_level
    target_item["adapter"] = adapter.name

    s_data.setdefault("history", []).append({
        "action": "steer_dispatched",
        "steer_id": target_item["steer_id"],
        "task_id": task_id,
        "instruction": target_item["instruction"],
        "operator": target_item.get("operator"),
        "urgent": target_item.get("urgent", False),
        "protocol": adapter.protocol_level,
        "adapter": adapter.name,
        "timestamp": now,
    })
    save_steering_data(s_data)

    if task:
        task["last_steered_at"] = now
        steering_history = list(task.get("steering_history") or [])
        steering_history.append({
            "steer_id": target_item["steer_id"],
            "instruction": target_item["instruction"],
            "operator": target_item.get("operator"),
            "urgent": target_item.get("urgent", False),
            "protocol": adapter.protocol_level,
            "adapter": adapter.name,
            "dispatched_at": now,
        })
        task["steering_history"] = steering_history
        save_tasks_data(tasks_data)

    return {
        "ok": True,  # "ok" = steer was recorded as dispatched, regardless of TTY delivery
        "steer_id": target_item["steer_id"],
        "task_id": task_id,
        "status": "dispatched",
        "protocol": adapter.protocol_level,
        "adapter": adapter.name,
        "pane_delivery_ok": steer_result.get("ok", True),  # TTY-level send result for observability
        "dispatched_at": target_item["dispatched_at"],
    }


def halt_task(
    task_id: str,
    reason: str = "human interrupt",
    operator: str = "human",
    execute_kill: bool = True,
) -> Dict[str, Any]:
    """Perform a graceful soft halt (SIGINT / ctrl-c) on a task via its AgentAdapter."""
    tasks_data = load_tasks_data()
    task = next((t for t in tasks_data.get("tasks", []) if t.get("task_id") == task_id), None)
    if not task:
        raise ValueError(f"Task '{task_id}' not found")

    pane_id = task.get("pane_id")
    agent_name = task.get("agent")
    adapter = get_agent_adapter(agent_name)

    if pane_id and execute_kill:
        adapter.interrupt(pane_id, reason=reason)

    old_status = task.get("status")
    now = time.time()
    task["status"] = "interrupted"
    task["interrupt_reason"] = reason
    task["interrupted_by"] = operator
    task["interrupted_at"] = now
    task["protocol"] = adapter.protocol_level
    task["adapter"] = adapter.name
    task.setdefault("status_history", []).append({
        "from": old_status,
        "to": "interrupted",
        "reason": reason,
        "operator": operator,
        "protocol": adapter.protocol_level,
        "adapter": adapter.name,
        "timestamp": now,
    })
    save_tasks_data(tasks_data)

    # Record in steering history
    s_data = load_steering_data()
    s_data.setdefault("history", []).append({
        "action": "task_halted",
        "task_id": task_id,
        "reason": reason,
        "operator": operator,
        "protocol": adapter.protocol_level,
        "adapter": adapter.name,
        "timestamp": now,
    })
    save_steering_data(s_data)

    return {
        "ok": True,
        "task_id": task_id,
        "status": "interrupted",
        "reason": reason,
        "operator": operator,
        "protocol": adapter.protocol_level,
        "adapter": adapter.name,
        "timestamp": now,
    }


def list_task_steers(task_id: str) -> List[Dict[str, Any]]:
    """List all steer instructions for a given task."""
    s_data = load_steering_data()
    return s_data.get("steering_queues", {}).get(task_id, [])
