#!/usr/bin/env python3
"""Herdr Kernel Control Primitives (Phase 1).

Provides first-class runtime control primitives for human and automated steering:
- Pause / Resume (workflow-level and node-level)
- Step execution (single-step progression while paused)
- Rollback (DAG topological downstream invalidation & state reset)
- Force Pass (gate override with human audit trail)
- Checkpoint Snapshots (durable point-in-time state capture & restoration)
"""

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from . import workflow
from . import state_db


HOME = Path.home()
CONTROLLER_DIR = HOME / ".herdr-controller"


def get_workflows_file() -> Path:
    return Path(os.environ.get("WORKFLOWS_FILE") or (CONTROLLER_DIR / "workflows.json"))


def get_tasks_file() -> Path:
    return Path(os.environ.get("TASKS_FILE") or (CONTROLLER_DIR / "tasks.json"))


def get_checkpoints_dir() -> Path:
    return Path(os.environ.get("CHECKPOINTS_DIR") or (CONTROLLER_DIR / "checkpoints"))


def get_stage_state_file() -> Path:
    return Path(os.environ.get("STAGE_STATE_FILE") or (CONTROLLER_DIR / "stage-state.json"))


def _atomic_write_json(file_path: Path, data: Any) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_suffix(f".tmp.{os.getpid()}.{time.time_ns()}")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, file_path)


def load_workflows_data() -> Dict[str, Any]:
    wf_file = get_workflows_file()
    if not wf_file.exists():
        return {"workflows": {}}
    try:
        with open(wf_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {"workflows": {}}
            data.setdefault("workflows", {})
            return data
    except (OSError, json.JSONDecodeError):
        return {"workflows": {}}


def save_workflows_data(data: Dict[str, Any]) -> None:
    _atomic_write_json(get_workflows_file(), data)


def load_tasks_data() -> Dict[str, Any]:
    tasks_file = get_tasks_file()
    if not tasks_file.exists():
        return {"tasks": []}
    try:
        with open(tasks_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {"tasks": []}
            data.setdefault("tasks", [])
            return data
    except (OSError, json.JSONDecodeError):
        return {"tasks": []}


def save_tasks_data(data: Dict[str, Any]) -> None:
    _atomic_write_json(get_tasks_file(), data)


def collect_downstream_nodes(nodes_by_id: Dict[str, Dict[str, Any]], root_id: str) -> Set[str]:
    """Calculate the downstream closure containing root_id and all its reachable successors."""
    dependents: Dict[str, Set[str]] = {}
    for node in nodes_by_id.values():
        for dep in node.get("depends_on", []):
            dependents.setdefault(dep, set()).add(node["id"])

    seen = {root_id}
    frontier = [root_id]
    while frontier:
        curr = frontier.pop()
        for nxt in dependents.get(curr, ()):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return seen


# ============================================================
# 1. Pause & Resume Primitives
# ============================================================

def pause_workflow(workflow_id: str, node_id: Optional[str] = None) -> Dict[str, Any]:
    """Pause automatic workflow progression globally or hold a specific node."""
    data = load_workflows_data()
    wfs = data.get("workflows", {})
    if workflow_id not in wfs:
        raise ValueError(f"Workflow '{workflow_id}' not found")

    target = wfs[workflow_id]
    if node_id:
        paused_nodes = list(target.get("paused_nodes") or [])
        if node_id not in paused_nodes:
            paused_nodes.append(node_id)
        target["paused_nodes"] = paused_nodes
        save_workflows_data(data)
        return {
            "ok": True,
            "workflow_id": workflow_id,
            "status": target.get("status"),
            "node_id": node_id,
            "paused_nodes": paused_nodes,
        }

    target["status"] = "paused"
    save_workflows_data(data)
    return {
        "ok": True,
        "workflow_id": workflow_id,
        "status": "paused",
    }


def resume_workflow(workflow_id: str, node_id: Optional[str] = None) -> Dict[str, Any]:
    """Resume automatic workflow progression globally or unpause a specific node."""
    data = load_workflows_data()
    wfs = data.get("workflows", {})
    if workflow_id not in wfs:
        raise ValueError(f"Workflow '{workflow_id}' not found")

    target = wfs[workflow_id]
    if node_id:
        paused_nodes = list(target.get("paused_nodes") or [])
        if node_id in paused_nodes:
            paused_nodes.remove(node_id)
        target["paused_nodes"] = paused_nodes
        save_workflows_data(data)
        return {
            "ok": True,
            "workflow_id": workflow_id,
            "status": target.get("status"),
            "node_id": node_id,
            "paused_nodes": paused_nodes,
        }

    target["status"] = "running"
    save_workflows_data(data)
    return {
        "ok": True,
        "workflow_id": workflow_id,
        "status": "running",
    }


# ============================================================
# 2. Force Pass Gate Primitive
# ============================================================

def force_pass_gate(
    workflow_id: str,
    gate_node_id: str,
    note: str = "human forced pass",
    operator: str = "human",
) -> Dict[str, Any]:
    """Forcibly mark a gate node verdict as passed/approved with an audit note."""
    tasks_data = load_tasks_data()
    updated_tasks = []

    for task in tasks_data.get("tasks", []):
        if task.get("workflow_id") != workflow_id:
            continue
        if gate_node_id not in (task.get("node"), task.get("stage")):
            continue
        if task.get("status") == "superseded":
            continue

        task["stage_verdict"] = "pass"
        task["stage_verdict_note"] = f"[FORCE PASS by {operator}] {note}"
        task["updated_at"] = time.time()
        updated_tasks.append(task.get("task_id"))

    save_tasks_data(tasks_data)

    # Also record in workflow entry gate_overrides
    wf_data = load_workflows_data()
    if workflow_id in wf_data.get("workflows", {}):
        wf_entry = wf_data["workflows"][workflow_id]
        overrides = dict(wf_entry.get("gate_overrides") or {})
        overrides[gate_node_id] = {
            "verdict": "pass",
            "note": note,
            "operator": operator,
            "timestamp": time.time(),
        }
        wf_entry["gate_overrides"] = overrides
        save_workflows_data(wf_data)

    return {
        "ok": True,
        "workflow_id": workflow_id,
        "gate_node_id": gate_node_id,
        "updated_tasks": updated_tasks,
    }


# ============================================================
# 3. Rollback Primitive
# ============================================================

def rollback_workflow(
    workflow_id: str,
    target_node_id: str,
    reason: str = "manual_rollback",
) -> Dict[str, Any]:
    """Roll back workflow execution to target_node_id, invalidating downstream tasks and locks."""
    wf_data = load_workflows_data()
    wf_entry = wf_data.get("workflows", {}).get(workflow_id)
    if not wf_entry:
        raise ValueError(f"Workflow '{workflow_id}' not found")

    cfg = wf_entry.get("config") or {}
    norm_cfg = workflow.normalize_workflow(cfg)
    nodes = norm_cfg.get("nodes", [])
    nodes_by_id = {n["id"]: n for n in nodes}

    if target_node_id not in nodes_by_id:
        raise ValueError(
            f"Target node '{target_node_id}' not in workflow nodes ({list(nodes_by_id.keys())})"
        )

    affected_nodes = collect_downstream_nodes(nodes_by_id, target_node_id)

    tasks_data = load_tasks_data()
    invalidated = []

    for task in tasks_data.get("tasks", []):
        if task.get("workflow_id") != workflow_id:
            continue
        task_node = task.get("node") or task.get("stage")
        if task_node not in affected_nodes:
            continue
        if task.get("status") == "superseded":
            continue

        task["status"] = "superseded"
        task["superseded_reason"] = f"rollback to {target_node_id}: {reason}"
        task["updated_at"] = time.time()
        invalidated.append(task.get("task_id"))

    save_tasks_data(tasks_data)

    # Clean up stage advance locks in workflow entry and stage-state.json
    advances = dict(wf_entry.get("stage_advancing") or {})
    for n_id in affected_nodes:
        advances.pop(n_id, None)
    wf_entry["stage_advancing"] = advances

    s_file = get_stage_state_file()
    if s_file.exists():
        try:
            with open(s_file, "r", encoding="utf-8") as fp:
                s_data = json.load(fp)
            if isinstance(s_data, dict):
                changed = False
                for n_id in affected_nodes:
                    k = f"{workflow_id}:{n_id}"
                    if k in s_data:
                        del s_data[k]
                        changed = True
                if changed:
                    _atomic_write_json(s_file, s_data)
        except Exception:
            pass

    # Record rollback audit history
    history = list(wf_entry.get("history") or [])
    history.append({
        "action": "rollback",
        "target_node_id": target_node_id,
        "reason": reason,
        "affected_nodes": list(affected_nodes),
        "invalidated_tasks": invalidated,
        "timestamp": time.time(),
    })
    wf_entry["history"] = history
    save_workflows_data(wf_data)

    return {
        "ok": True,
        "workflow_id": workflow_id,
        "target_node_id": target_node_id,
        "affected_nodes": list(affected_nodes),
        "invalidated_tasks": invalidated,
    }


# ============================================================
# 4. Step Primitive
# ============================================================

def _is_task_completed(task: Dict[str, Any]) -> bool:
    status = task.get("status")
    if status in ("completed", "cleaned", "cleanup_ready"):
        verdict = task.get("stage_verdict")
        return verdict != "blocked"
    return False


def step_workflow(workflow_id: str) -> Dict[str, Any]:
    """Execute a single step: compute ready nodes and dispatch or advance exactly one ready node."""
    wf_data = load_workflows_data()
    wf_entry = wf_data.get("workflows", {}).get(workflow_id)
    if not wf_entry:
        raise ValueError(f"Workflow '{workflow_id}' not found")

    cfg = wf_entry.get("config") or {}
    norm_cfg = workflow.normalize_workflow(cfg)
    nodes = norm_cfg.get("nodes", [])
    if not nodes:
        raise ValueError(f"Workflow '{workflow_id}' has no nodes in config")

    tasks_data = load_tasks_data()
    wf_tasks = [t for t in tasks_data.get("tasks", []) if t.get("workflow_id") == workflow_id]

    completed_nodes: Set[str] = set()
    for n in nodes:
        n_id = n["id"]
        n_tasks = [
            t for t in wf_tasks
            if t.get("status") != "superseded" and (t.get("node") == n_id or t.get("stage") == n_id)
        ]
        if n_tasks and any(_is_task_completed(t) for t in n_tasks):
            completed_nodes.add(n_id)

    ready_nodes = workflow.get_ready_nodes(norm_cfg, completed_nodes)
    paused_nodes = set(wf_entry.get("paused_nodes") or [])
    eligible_ready = [n for n in ready_nodes if n["id"] not in paused_nodes]

    if not eligible_ready:
        return {
            "ok": False,
            "workflow_id": workflow_id,
            "reason": "no_ready_nodes",
            "ready_nodes": [],
        }

    stepped = eligible_ready[0]
    stepped_id = stepped["id"]

    # Keep workflow paused to enforce single-step execution control
    wf_entry["status"] = "paused"
    save_workflows_data(wf_data)

    return {
        "ok": True,
        "workflow_id": workflow_id,
        "stepped_node": stepped_id,
        "stepped_label": stepped.get("label", stepped_id),
        "remaining_ready": [n["id"] for n in eligible_ready[1:]],
    }


# ============================================================
# 5. Checkpoint Snapshot Primitives
# ============================================================

def create_checkpoint(
    workflow_id: str,
    tag: Optional[str] = None,
    parent_checkpoint_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Capture a durable point-in-time snapshot of the workflow and its tasks across JSON & SQLite."""
    wf_data = load_workflows_data()
    wf_entry = wf_data.get("workflows", {}).get(workflow_id)
    if not wf_entry:
        raise ValueError(f"Workflow '{workflow_id}' not found")

    tasks_data = load_tasks_data()
    wf_tasks = [t for t in tasks_data.get("tasks", []) if t.get("workflow_id") == workflow_id]

    now = time.time()
    ts_str = time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
    short_uuid = uuid.uuid4().hex[:6]
    cp_id = f"cp_{workflow_id}_{ts_str}_{short_uuid}"

    snapshot = {
        "checkpoint_id": cp_id,
        "workflow_id": workflow_id,
        "tag": tag or "",
        "parent_checkpoint_id": parent_checkpoint_id,
        "created_at": now,
        "workflow": wf_entry,
        "tasks": wf_tasks,
    }

    # 1. Write V1 JSON file for backward compatibility
    cp_dir = get_checkpoints_dir() / workflow_id
    cp_dir.mkdir(parents=True, exist_ok=True)
    cp_file = cp_dir / f"{cp_id}.json"
    _atomic_write_json(cp_file, snapshot)

    # 2. Write to V2 SQLite state_db
    try:
        state_db.init_db()
        state_db.save_workflow(wf_entry)
        for t in wf_tasks:
            state_db.save_task(t)
        state_db.create_checkpoint(
            workflow_id=workflow_id,
            checkpoint_id=cp_id,
            tag=tag,
            parent_checkpoint_id=parent_checkpoint_id,
        )
    except Exception:
        pass

    return {
        "ok": True,
        "workflow_id": workflow_id,
        "checkpoint_id": cp_id,
        "tag": tag or "",
        "parent_checkpoint_id": parent_checkpoint_id,
        "created_at": now,
        "task_count": len(wf_tasks),
        "path": str(cp_file),
    }


def list_checkpoints(workflow_id: str) -> List[Dict[str, Any]]:
    """List all available checkpoints for workflow_id, unifying SQLite & JSON files."""
    by_id: Dict[str, Dict[str, Any]] = {}

    # 1. Fetch from SQLite V2 store
    try:
        db_cps = state_db.list_checkpoints(workflow_id)
        for c in db_cps:
            by_id[c["checkpoint_id"]] = c
    except Exception:
        pass

    # 2. Fetch from V1 JSON directory
    cp_dir = get_checkpoints_dir() / workflow_id
    if cp_dir.exists():
        for f in cp_dir.glob("cp_*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    snap = json.load(fp)
                    cpid = snap.get("checkpoint_id")
                    if cpid and cpid not in by_id:
                        by_id[cpid] = {
                            "checkpoint_id": cpid,
                            "workflow_id": snap.get("workflow_id"),
                            "tag": snap.get("tag", ""),
                            "parent_checkpoint_id": snap.get("parent_checkpoint_id"),
                            "created_at": snap.get("created_at", 0),
                            "task_count": len(snap.get("tasks", [])),
                            "workflow_status": snap.get("workflow", {}).get("status"),
                        }
            except Exception:
                continue

    results = list(by_id.values())
    results.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return results


def get_checkpoint(workflow_id: str, checkpoint_id: str) -> Dict[str, Any]:
    """Retrieve full snapshot payload for a checkpoint from SQLite or JSON."""
    # Try SQLite first
    try:
        return state_db.get_checkpoint(workflow_id, checkpoint_id)
    except Exception:
        pass

    # Fallback to JSON file
    cp_file = get_checkpoints_dir() / workflow_id / f"{checkpoint_id}.json"
    if not cp_file.exists():
        # Search all workflow subdirectories if workflow_id was omitted or uncertain
        matches = list(get_checkpoints_dir().glob(f"*/{checkpoint_id}.json"))
        if matches:
            cp_file = matches[0]
        else:
            raise FileNotFoundError(f"Checkpoint '{checkpoint_id}' not found for workflow '{workflow_id}'")

    with open(cp_file, "r", encoding="utf-8") as f:
        return json.load(f)


def restore_checkpoint(workflow_id: str, checkpoint_id: str) -> Dict[str, Any]:
    """Restore workflow state and tasks from a checkpoint snapshot in both JSON and SQLite."""
    snapshot = get_checkpoint(workflow_id, checkpoint_id)
    restored_wf = snapshot.get("workflow")
    restored_tasks = snapshot.get("tasks", [])

    if not restored_wf:
        raise ValueError(f"Checkpoint '{checkpoint_id}' contains no workflow data")

    # 1. Update workflows.json
    wf_data = load_workflows_data()
    wf_data.setdefault("workflows", {})[workflow_id] = restored_wf
    save_workflows_data(wf_data)

    # 2. Update tasks.json (replace tasks for this workflow with snapshot tasks)
    tasks_data = load_tasks_data()
    existing_tasks = [t for t in tasks_data.get("tasks", []) if t.get("workflow_id") != workflow_id]
    existing_tasks.extend(restored_tasks)
    tasks_data["tasks"] = existing_tasks
    save_tasks_data(tasks_data)

    # 3. Restore in SQLite state_db
    try:
        state_db.restore_checkpoint(workflow_id, checkpoint_id)
    except Exception:
        pass

    # 4. Clean up any transient stage advance locks in stage-state.json
    s_file = get_stage_state_file()
    if s_file.exists():
        try:
            with open(s_file, "r", encoding="utf-8") as fp:
                s_data = json.load(fp)
            if isinstance(s_data, dict):
                to_del = [k for k in s_data if k.startswith(f"{workflow_id}:")]
                if to_del:
                    for k in to_del:
                        del s_data[k]
                    _atomic_write_json(s_file, s_data)
        except Exception:
            pass

    return {
        "ok": True,
        "workflow_id": workflow_id,
        "checkpoint_id": checkpoint_id,
        "restored_tasks": len(restored_tasks),
        "status": restored_wf.get("status"),
    }


def fork_workflow_from_checkpoint(
    checkpoint_id: str,
    new_workflow_id: str,
    new_title: Optional[str] = None,
) -> Dict[str, Any]:
    """Time-travel branching: Fork a new workflow instance from a historical checkpoint across JSON & SQLite."""
    # 1. Primary path: Fork directly via SQLite state_db
    try:
        res = state_db.fork_workflow_from_checkpoint(
            checkpoint_id=checkpoint_id,
            new_workflow_id=new_workflow_id,
            new_title=new_title,
        )
        # Sync the forked workflow & tasks to workflows.json and tasks.json
        forked_wf = state_db.get_workflow(new_workflow_id)
        if forked_wf:
            wf_data = load_workflows_data()
            wf_data.setdefault("workflows", {})[new_workflow_id] = forked_wf
            save_workflows_data(wf_data)

        forked_tasks = state_db.get_tasks(new_workflow_id)
        if forked_tasks:
            tasks_data = load_tasks_data()
            tasks_data.setdefault("tasks", []).extend(forked_tasks)
            save_tasks_data(tasks_data)

        return res
    except FileNotFoundError:
        pass
    except Exception:
        pass

    # 2. Fallback path: locate legacy JSON checkpoint file
    matches = list(get_checkpoints_dir().glob(f"*/{checkpoint_id}.json"))
    if not matches:
        raise FileNotFoundError(f"Source checkpoint '{checkpoint_id}' not found")
    with open(matches[0], "r", encoding="utf-8") as f:
        snapshot = json.load(f)

    source_wf = snapshot.get("workflow", {})
    source_tasks = snapshot.get("tasks", [])

    # Seed snapshot into state_db and delegate fork
    try:
        if source_wf:
            state_db.save_workflow(source_wf)
        for t in source_tasks:
            state_db.save_task(t)
        state_db.create_checkpoint(
            workflow_id=snapshot.get("workflow_id", ""),
            checkpoint_id=checkpoint_id,
            tag=snapshot.get("tag"),
            parent_checkpoint_id=snapshot.get("parent_checkpoint_id"),
        )
        return fork_workflow_from_checkpoint(checkpoint_id, new_workflow_id, new_title)
    except Exception:
        # Ultimate standalone JSON fallback
        now = time.time()
        forked_wf = dict(source_wf)
        forked_wf["workflow_id"] = new_workflow_id
        forked_wf["title"] = new_title or f"{source_wf.get('title', 'Workflow')} (Forked from {checkpoint_id[:12]})"
        forked_wf["created_at"] = now
        forked_wf["updated_at"] = now
        forked_wf["forked_from"] = {
            "source_workflow_id": snapshot.get("workflow_id"),
            "source_checkpoint_id": checkpoint_id,
            "forked_at": now,
        }
        forked_tasks = []
        for t in source_tasks:
            t_clone = dict(t)
            orig_tid = t.get("task_id", "")
            t_clone["task_id"] = f"{orig_tid}-fork-{uuid.uuid4().hex[:6]}"
            t_clone["workflow_id"] = new_workflow_id
            t_clone["created_at"] = now
            forked_tasks.append(t_clone)

        wf_data = load_workflows_data()
        wf_data.setdefault("workflows", {})[new_workflow_id] = forked_wf
        save_workflows_data(wf_data)

        tasks_data = load_tasks_data()
        tasks_data.setdefault("tasks", []).extend(forked_tasks)
        save_tasks_data(tasks_data)

        return {
            "ok": True,
            "new_workflow_id": new_workflow_id,
            "new_title": forked_wf["title"],
            "source_checkpoint_id": checkpoint_id,
            "source_workflow_id": snapshot.get("workflow_id"),
            "cloned_tasks": len(forked_tasks),
        }


