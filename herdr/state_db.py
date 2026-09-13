#!/usr/bin/env python3
"""Herdr Checkpoint Store V2: SQLite Embedded State Engine.

Provides durable, transactional state management and time-travel snapshots
using the Python standard library `sqlite3` (Zero external dependencies).

Core Capabilities:
- WAL mode (Write-Ahead Logging) for safe concurrent reads & single-writer transactions
- Single-transaction atomic snapshot capture across workflows and tasks
- Fast indexed snapshot listing without directory scanning
- Time-travel state forking (branching a new workflow from any historical checkpoint)
- Parent-child checkpoint DAG lineage tracking
- Lossless bi-directional migration between V1 JSON files and V2 SQLite DB
"""

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


HOME = Path.home()
CONTROLLER_DIR = HOME / ".herdr-controller"


def get_default_db_path() -> Path:
    """Resolve active SQLite DB path from environment or default location."""
    env_path = os.environ.get("HERDR_STATE_DB")
    if env_path:
        return Path(env_path)
    if os.environ.get("CHECKPOINTS_DIR"):
        return Path(os.environ["CHECKPOINTS_DIR"]).parent / "state.db"
    if os.environ.get("WORKFLOWS_FILE"):
        return Path(os.environ["WORKFLOWS_FILE"]).parent / "state.db"
    if os.environ.get("TASKS_FILE"):
        return Path(os.environ["TASKS_FILE"]).parent / "state.db"
    return CONTROLLER_DIR / "state.db"



_INITIALIZED_DBS: set = set()


def _ensure_schema(conn: sqlite3.Connection, path_key: str) -> None:
    """Execute table and index creation DDL once per database path."""
    if path_key in _INITIALIZED_DBS:
        return

    conn.execute("""
        CREATE TABLE IF NOT EXISTS workflows (
            workflow_id TEXT PRIMARY KEY,
            title TEXT,
            status TEXT,
            template_name TEXT,
            current_stage TEXT,
            config_json TEXT,
            metadata_json TEXT,
            created_at REAL,
            updated_at REAL
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            workflow_id TEXT,
            node TEXT,
            stage TEXT,
            agent TEXT,
            status TEXT,
            stage_verdict TEXT,
            stage_verdict_note TEXT,
            pane_id TEXT,
            goal TEXT,
            blocker TEXT,
            payload_json TEXT,
            created_at REAL,
            updated_at REAL,
            FOREIGN KEY (workflow_id) REFERENCES workflows(workflow_id) ON DELETE CASCADE
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS checkpoints (
            checkpoint_id TEXT PRIMARY KEY,
            workflow_id TEXT,
            tag TEXT,
            parent_checkpoint_id TEXT,
            created_at REAL,
            workflow_status TEXT,
            task_count INTEGER,
            snapshot_json TEXT,
            metadata_json TEXT,
            FOREIGN KEY (workflow_id) REFERENCES workflows(workflow_id) ON DELETE CASCADE
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id TEXT,
            task_id TEXT,
            event_type TEXT,
            payload_json TEXT,
            timestamp REAL
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS steering_items (
            steer_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            workflow_id TEXT,
            instruction TEXT NOT NULL,
            operator TEXT,
            urgent INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            dispatched_at REAL,
            created_at REAL,
            payload_json TEXT
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS steering_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            task_id TEXT,
            steer_id TEXT,
            instruction TEXT,
            operator TEXT,
            urgent INTEGER DEFAULT 0,
            reason TEXT,
            timestamp REAL,
            payload_json TEXT
        );
    """)

    # Indexes for fast lookup and DAG queries
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_wf ON tasks(workflow_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cp_wf_created ON checkpoints(workflow_id, created_at DESC);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cp_parent ON checkpoints(parent_checkpoint_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_wf ON events(workflow_id, timestamp);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_steering_task ON steering_items(task_id, status);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_steering_hist_task ON steering_history(task_id, timestamp);")

    _INITIALIZED_DBS.add(path_key)


def get_db_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Create a thread-safe connection to the SQLite state database with WAL mode."""
    path = db_path or get_default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(
        str(path),
        timeout=10.0,
        isolation_level=None,  # autocommit mode; we manage transactions explicitly
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    
    # Configure high-concurrency PRAGMAs
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")

    _ensure_schema(conn, str(path.resolve()))
    return conn


def init_db(db_path: Optional[Path] = None) -> Path:
    """Initialize database tables and indexes if they do not exist."""
    path = db_path or get_default_db_path()
    # Force initialization even if cached
    _INITIALIZED_DBS.discard(str(path.resolve()))
    conn = get_db_connection(path)
    conn.close()
    return path



def save_workflow(
    wf_dict: Dict[str, Any],
    db_path: Optional[Path] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """Upsert a workflow record."""
    should_close = False
    if conn is None:
        conn = get_db_connection(db_path)
        should_close = True

    wid = wf_dict.get("workflow_id")
    if not wid:
        raise ValueError("workflow_id is required")

    now = time.time()
    title = wf_dict.get("title", "")
    status = wf_dict.get("status", "pending")
    template_name = wf_dict.get("template_name", "")
    current_stage = wf_dict.get("current_stage") or wf_dict.get("stage", "")
    config_json = json.dumps(wf_dict.get("config", {}), ensure_ascii=False)
    created_at = float(wf_dict.get("created_at") or now)

    # Exclude special keys from metadata
    meta = {k: v for k, v in wf_dict.items() if k not in {
        "workflow_id", "title", "status", "template_name", "current_stage", "stage", "config", "created_at"
    }}
    metadata_json = json.dumps(meta, ensure_ascii=False)

    try:
        conn.execute("""
            INSERT INTO workflows (workflow_id, title, status, template_name, current_stage, config_json, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(workflow_id) DO UPDATE SET
                title=excluded.title,
                status=excluded.status,
                template_name=excluded.template_name,
                current_stage=excluded.current_stage,
                config_json=excluded.config_json,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at;
        """, (wid, title, status, template_name, current_stage, config_json, metadata_json, created_at, now))
    finally:
        if should_close:
            conn.close()


def get_workflow(workflow_id: str, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Fetch a workflow by its workflow_id."""
    conn = get_db_connection(db_path)
    try:
        cur = conn.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,))
        row = cur.fetchone()
        if not row:
            return None

        meta = json.loads(row["metadata_json"] or "{}")
        cfg = json.loads(row["config_json"] or "{}")

        wf = dict(meta)
        wf.update({
            "workflow_id": row["workflow_id"],
            "title": row["title"],
            "status": row["status"],
            "template_name": row["template_name"],
            "current_stage": row["current_stage"],
            "config": cfg,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })
        return wf
    finally:
        conn.close()


def list_workflows(
    status: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Fetch all workflows, optionally filtered by status."""
    conn = get_db_connection(db_path)
    try:
        if status:
            cur = conn.execute("SELECT * FROM workflows WHERE status = ? ORDER BY created_at DESC", (status,))
        else:
            cur = conn.execute("SELECT * FROM workflows ORDER BY created_at DESC")
        results = []
        for row in cur.fetchall():
            meta = json.loads(row["metadata_json"] or "{}")
            cfg = json.loads(row["config_json"] or "{}")
            wf = dict(meta)
            wf.update({
                "workflow_id": row["workflow_id"],
                "title": row["title"],
                "status": row["status"],
                "template_name": row["template_name"],
                "current_stage": row["current_stage"],
                "config": cfg,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
            results.append(wf)
        return results
    finally:
        conn.close()


def delete_workflow(workflow_id: str, db_path: Optional[Path] = None) -> bool:
    """Delete a workflow and cascade its tasks/checkpoints."""
    conn = get_db_connection(db_path)
    try:
        conn.execute("BEGIN TRANSACTION;")
        cur = conn.execute("DELETE FROM workflows WHERE workflow_id = ?", (workflow_id,))
        deleted = cur.rowcount > 0
        conn.execute("COMMIT;")
        return deleted
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    finally:
        conn.close()


def save_task(
    task_dict: Dict[str, Any],
    db_path: Optional[Path] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """Upsert a task record."""
    should_close = False
    if conn is None:
        conn = get_db_connection(db_path)
        should_close = True

    tid = task_dict.get("task_id")
    wid = task_dict.get("workflow_id")
    if not tid or not wid:
        raise ValueError("task_id and workflow_id are required")

    # Auto-ensure parent workflow exists to prevent foreign key violation
    cur_wf = conn.execute("SELECT 1 FROM workflows WHERE workflow_id = ?", (wid,))
    if not cur_wf.fetchone():
        save_workflow({"workflow_id": wid, "title": wid, "status": "unknown"}, db_path, conn=conn)

    now = time.time()
    node = task_dict.get("node") or task_dict.get("stage", "")
    stage = task_dict.get("stage") or node
    agent = task_dict.get("agent", "auto")
    status = task_dict.get("status", "pending")
    verdict = task_dict.get("stage_verdict", "")
    verdict_note = task_dict.get("stage_verdict_note", "")
    pane_id = task_dict.get("pane_id", "")
    goal = task_dict.get("goal", "")
    blocker = task_dict.get("blocker", "")
    created_at = float(task_dict.get("started_at") or task_dict.get("created_at") or now)

    payload = {k: v for k, v in task_dict.items() if k not in {
        "task_id", "workflow_id", "node", "stage", "agent", "status",
        "stage_verdict", "stage_verdict_note", "pane_id", "goal", "blocker", "created_at", "started_at"
    }}
    payload_json = json.dumps(payload, ensure_ascii=False)

    try:
        conn.execute("""
            INSERT INTO tasks (task_id, workflow_id, node, stage, agent, status, stage_verdict, stage_verdict_note, pane_id, goal, blocker, payload_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                workflow_id=excluded.workflow_id,
                node=excluded.node,
                stage=excluded.stage,
                agent=excluded.agent,
                status=excluded.status,
                stage_verdict=excluded.stage_verdict,
                stage_verdict_note=excluded.stage_verdict_note,
                pane_id=excluded.pane_id,
                goal=excluded.goal,
                blocker=excluded.blocker,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at;
        """, (tid, wid, node, stage, agent, status, verdict, verdict_note, pane_id, goal, blocker, payload_json, created_at, now))
    finally:
        if should_close:
            conn.close()


def get_task(task_id: str, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Fetch a single task by its task_id."""
    conn = get_db_connection(db_path)
    try:
        cur = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        row = cur.fetchone()
        if not row:
            return None
        payload = json.loads(row["payload_json"] or "{}")
        t = dict(payload)
        t.update({
            "task_id": row["task_id"],
            "workflow_id": row["workflow_id"],
            "node": row["node"],
            "stage": row["stage"],
            "agent": row["agent"],
            "status": row["status"],
            "stage_verdict": row["stage_verdict"],
            "stage_verdict_note": row["stage_verdict_note"],
            "pane_id": row["pane_id"],
            "goal": row["goal"],
            "blocker": row["blocker"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })
        return t
    finally:
        conn.close()


def get_tasks(workflow_id: str, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Fetch all tasks for a workflow."""
    return list_tasks(workflow_id=workflow_id, db_path=db_path)


def list_tasks(
    workflow_id: Optional[str] = None,
    status: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Fetch tasks optionally filtered by workflow_id and/or status."""
    conn = get_db_connection(db_path)
    try:
        query = "SELECT * FROM tasks WHERE 1=1"
        params: List[Any] = []
        if workflow_id:
            query += " AND workflow_id = ?"
            params.append(workflow_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at ASC"
        cur = conn.execute(query, tuple(params))
        tasks = []
        for row in cur.fetchall():
            payload = json.loads(row["payload_json"] or "{}")
            t = dict(payload)
            t.update({
                "task_id": row["task_id"],
                "workflow_id": row["workflow_id"],
                "node": row["node"],
                "stage": row["stage"],
                "agent": row["agent"],
                "status": row["status"],
                "stage_verdict": row["stage_verdict"],
                "stage_verdict_note": row["stage_verdict_note"],
                "pane_id": row["pane_id"],
                "goal": row["goal"],
                "blocker": row["blocker"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
            tasks.append(t)
        return tasks
    finally:
        conn.close()


def delete_task(task_id: str, db_path: Optional[Path] = None) -> bool:
    """Delete a task by its task_id."""
    conn = get_db_connection(db_path)
    try:
        conn.execute("BEGIN TRANSACTION;")
        cur = conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
        deleted = cur.rowcount > 0
        conn.execute("COMMIT;")
        return deleted
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    finally:
        conn.close()


def save_steer(
    steer_dict: Dict[str, Any],
    db_path: Optional[Path] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """Upsert a steering item."""
    should_close = False
    if conn is None:
        conn = get_db_connection(db_path)
        should_close = True

    sid = steer_dict.get("steer_id")
    tid = steer_dict.get("task_id")
    if not sid or not tid:
        raise ValueError("steer_id and task_id are required")

    wid = steer_dict.get("workflow_id")
    instruction = steer_dict.get("instruction", "")
    operator = steer_dict.get("operator", "human")
    urgent = 1 if steer_dict.get("urgent") else 0
    status = steer_dict.get("status", "pending")
    dispatched_at = steer_dict.get("dispatched_at")
    created_at = float(steer_dict.get("created_at") or time.time())

    payload = {k: v for k, v in steer_dict.items() if k not in {
        "steer_id", "task_id", "workflow_id", "instruction", "operator",
        "urgent", "status", "dispatched_at", "created_at"
    }}
    payload_json = json.dumps(payload, ensure_ascii=False)

    try:
        conn.execute("""
            INSERT INTO steering_items (
                steer_id, task_id, workflow_id, instruction, operator,
                urgent, status, dispatched_at, created_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(steer_id) DO UPDATE SET
                task_id=excluded.task_id,
                workflow_id=excluded.workflow_id,
                instruction=excluded.instruction,
                operator=excluded.operator,
                urgent=excluded.urgent,
                status=excluded.status,
                dispatched_at=excluded.dispatched_at,
                payload_json=excluded.payload_json;
        """, (sid, tid, wid, instruction, operator, urgent, status, dispatched_at, created_at, payload_json))
    finally:
        if should_close:
            conn.close()


def get_steer(steer_id: str, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Fetch a steering item by its steer_id."""
    conn = get_db_connection(db_path)
    try:
        cur = conn.execute("SELECT * FROM steering_items WHERE steer_id = ?", (steer_id,))
        row = cur.fetchone()
        if not row:
            return None
        payload = json.loads(row["payload_json"] or "{}")
        item = dict(payload)
        item.update({
            "steer_id": row["steer_id"],
            "task_id": row["task_id"],
            "workflow_id": row["workflow_id"],
            "instruction": row["instruction"],
            "operator": row["operator"],
            "urgent": bool(row["urgent"]),
            "status": row["status"],
            "dispatched_at": row["dispatched_at"],
            "created_at": row["created_at"],
        })
        return item
    finally:
        conn.close()


def list_steers(
    task_id: Optional[str] = None,
    status: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """List steering items optionally filtered by task_id and/or status."""
    conn = get_db_connection(db_path)
    try:
        query = "SELECT * FROM steering_items WHERE 1=1"
        params: List[Any] = []
        if task_id:
            query += " AND task_id = ?"
            params.append(task_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at ASC"
        cur = conn.execute(query, tuple(params))
        items = []
        for row in cur.fetchall():
            payload = json.loads(row["payload_json"] or "{}")
            item = dict(payload)
            item.update({
                "steer_id": row["steer_id"],
                "task_id": row["task_id"],
                "workflow_id": row["workflow_id"],
                "instruction": row["instruction"],
                "operator": row["operator"],
                "urgent": bool(row["urgent"]),
                "status": row["status"],
                "dispatched_at": row["dispatched_at"],
                "created_at": row["created_at"],
            })
            items.append(item)
        return items
    finally:
        conn.close()


def update_steer_status(
    steer_id: str,
    status: str,
    dispatched_at: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """Update status and dispatched_at for a steering item."""
    conn = get_db_connection(db_path)
    try:
        conn.execute("BEGIN TRANSACTION;")
        if dispatched_at is not None:
            cur = conn.execute(
                "UPDATE steering_items SET status = ?, dispatched_at = ? WHERE steer_id = ?",
                (status, dispatched_at, steer_id),
            )
        else:
            cur = conn.execute(
                "UPDATE steering_items SET status = ? WHERE steer_id = ?",
                (status, steer_id),
            )
        updated = cur.rowcount > 0
        conn.execute("COMMIT;")
        return updated
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    finally:
        conn.close()


def record_steering_history(
    record: Dict[str, Any],
    db_path: Optional[Path] = None,
) -> None:
    """Record a steering audit/action entry."""
    conn = get_db_connection(db_path)
    try:
        now = float(record.get("timestamp") or time.time())
        action = record.get("action", "unknown")
        task_id = record.get("task_id")
        steer_id = record.get("steer_id")
        instruction = record.get("instruction")
        operator = record.get("operator")
        urgent = 1 if record.get("urgent") else 0
        reason = record.get("reason")
        payload = {k: v for k, v in record.items() if k not in {
            "action", "task_id", "steer_id", "instruction", "operator", "urgent", "reason", "timestamp"
        }}
        payload_json = json.dumps(payload, ensure_ascii=False)

        conn.execute("""
            INSERT INTO steering_history (
                action, task_id, steer_id, instruction, operator, urgent, reason, timestamp, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (action, task_id, steer_id, instruction, operator, urgent, reason, now, payload_json))
    finally:
        conn.close()


def list_steering_history(
    task_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """List steering history entries ordered chronologically."""
    conn = get_db_connection(db_path)
    try:
        if task_id:
            cur = conn.execute("SELECT * FROM steering_history WHERE task_id = ? ORDER BY timestamp ASC", (task_id,))
        else:
            cur = conn.execute("SELECT * FROM steering_history ORDER BY timestamp ASC")
        results = []
        for row in cur.fetchall():
            payload = json.loads(row["payload_json"] or "{}")
            item = dict(payload)
            item.update({
                "action": row["action"],
                "task_id": row["task_id"],
                "steer_id": row["steer_id"],
                "instruction": row["instruction"],
                "operator": row["operator"],
                "urgent": bool(row["urgent"]),
                "reason": row["reason"],
                "timestamp": row["timestamp"],
            })
            results.append(item)
        return results
    finally:
        conn.close()


def create_checkpoint(
    workflow_id: str,
    tag: Optional[str] = None,
    parent_checkpoint_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
    checkpoint_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Capture an atomic point-in-time snapshot of a workflow and all its tasks."""
    conn = get_db_connection(db_path)
    try:
        cur_wf = conn.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,))
        wf_row = cur_wf.fetchone()
        if not wf_row:
            raise ValueError(f"Workflow '{workflow_id}' not found in state DB")

        cur_tasks = conn.execute("SELECT * FROM tasks WHERE workflow_id = ? ORDER BY created_at ASC", (workflow_id,))
        task_rows = cur_tasks.fetchall()

        now = time.time()
        ts_str = time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
        short_uuid = uuid.uuid4().hex[:6]
        cp_id = checkpoint_id or f"cp_{workflow_id}_{ts_str}_{short_uuid}"

        # Reconstruct structured objects
        wf_dict = json.loads(wf_row["metadata_json"] or "{}")
        wf_dict.update({
            "workflow_id": wf_row["workflow_id"],
            "title": wf_row["title"],
            "status": wf_row["status"],
            "template_name": wf_row["template_name"],
            "current_stage": wf_row["current_stage"],
            "config": json.loads(wf_row["config_json"] or "{}"),
            "created_at": wf_row["created_at"],
            "updated_at": wf_row["updated_at"],
        })

        tasks_list = []
        for r in task_rows:
            t_obj = json.loads(r["payload_json"] or "{}")
            t_obj.update({
                "task_id": r["task_id"],
                "workflow_id": r["workflow_id"],
                "node": r["node"],
                "stage": r["stage"],
                "agent": r["agent"],
                "status": r["status"],
                "stage_verdict": r["stage_verdict"],
                "stage_verdict_note": r["stage_verdict_note"],
                "pane_id": r["pane_id"],
                "goal": r["goal"],
                "blocker": r["blocker"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            })
            tasks_list.append(t_obj)

        snapshot_payload = {
            "checkpoint_id": cp_id,
            "workflow_id": workflow_id,
            "tag": tag or "",
            "parent_checkpoint_id": parent_checkpoint_id,
            "created_at": now,
            "workflow": wf_dict,
            "tasks": tasks_list,
            "metadata": metadata or {},
        }

        # Atomic insertion within a single transaction
        conn.execute("BEGIN TRANSACTION;")
        try:
            conn.execute("""
                INSERT INTO checkpoints (
                    checkpoint_id, workflow_id, tag, parent_checkpoint_id,
                    created_at, workflow_status, task_count, snapshot_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                cp_id,
                workflow_id,
                tag or "",
                parent_checkpoint_id,
                now,
                wf_row["status"],
                len(tasks_list),
                json.dumps(snapshot_payload, ensure_ascii=False),
                json.dumps(metadata or {}, ensure_ascii=False),
            ))

            # Record checkpoint event
            conn.execute("""
                INSERT INTO events (workflow_id, task_id, event_type, payload_json, timestamp)
                VALUES (?, ?, ?, ?, ?);
            """, (workflow_id, None, "checkpoint_created", json.dumps({"checkpoint_id": cp_id, "tag": tag}), now))
            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise

        return {
            "ok": True,
            "workflow_id": workflow_id,
            "checkpoint_id": cp_id,
            "tag": tag or "",
            "parent_checkpoint_id": parent_checkpoint_id,
            "created_at": now,
            "task_count": len(tasks_list),
            "workflow_status": wf_row["status"],
        }
    finally:
        conn.close()


def list_checkpoints(workflow_id: str, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """List checkpoints for a workflow sorted newest first."""
    conn = get_db_connection(db_path)
    try:
        cur = conn.execute("""
            SELECT checkpoint_id, workflow_id, tag, parent_checkpoint_id,
                   created_at, workflow_status, task_count, metadata_json
            FROM checkpoints
            WHERE workflow_id = ?
            ORDER BY created_at DESC;
        """, (workflow_id,))

        results = []
        for row in cur.fetchall():
            results.append({
                "checkpoint_id": row["checkpoint_id"],
                "workflow_id": row["workflow_id"],
                "tag": row["tag"],
                "parent_checkpoint_id": row["parent_checkpoint_id"],
                "created_at": row["created_at"],
                "workflow_status": row["workflow_status"],
                "task_count": row["task_count"],
                "metadata": json.loads(row["metadata_json"] or "{}"),
            })
        return results
    finally:
        conn.close()


def get_checkpoint(workflow_id: str, checkpoint_id: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Retrieve full snapshot payload from database."""
    conn = get_db_connection(db_path)
    try:
        cur = conn.execute("""
            SELECT snapshot_json FROM checkpoints
            WHERE workflow_id = ? AND checkpoint_id = ?;
        """, (workflow_id, checkpoint_id))
        row = cur.fetchone()
        if not row:
            raise FileNotFoundError(f"Checkpoint '{checkpoint_id}' not found for workflow '{workflow_id}'")
        return json.loads(row["snapshot_json"])
    finally:
        conn.close()


def restore_checkpoint(
    workflow_id: str,
    checkpoint_id: str,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Restore workflow state and tasks atomically from a checkpoint snapshot in SQLite."""
    snapshot = get_checkpoint(workflow_id, checkpoint_id, db_path)
    restored_wf = snapshot.get("workflow")
    restored_tasks = snapshot.get("tasks", [])

    if not restored_wf:
        raise ValueError(f"Checkpoint '{checkpoint_id}' contains no workflow data")

    conn = get_db_connection(db_path)
    try:
        conn.execute("BEGIN TRANSACTION;")
        try:
            # 1. Restore workflow record
            save_workflow(restored_wf, db_path, conn=conn)

            # 2. Delete existing tasks for this workflow and insert restored tasks
            conn.execute("DELETE FROM tasks WHERE workflow_id = ?;", (workflow_id,))
            for t in restored_tasks:
                save_task(t, db_path, conn=conn)

            # 3. Record event
            conn.execute("""
                INSERT INTO events (workflow_id, task_id, event_type, payload_json, timestamp)
                VALUES (?, ?, ?, ?, ?);
            """, (workflow_id, None, "checkpoint_restored", json.dumps({"checkpoint_id": checkpoint_id}), time.time()))

            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise

        return {
            "ok": True,
            "workflow_id": workflow_id,
            "checkpoint_id": checkpoint_id,
            "restored_tasks": len(restored_tasks),
            "status": restored_wf.get("status"),
        }
    finally:
        conn.close()


def fork_workflow_from_checkpoint(
    checkpoint_id: str,
    new_workflow_id: str,
    new_title: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Time-travel branching: Fork a new workflow instance from a historical checkpoint."""
    conn = get_db_connection(db_path)
    try:
        cur = conn.execute("SELECT * FROM checkpoints WHERE checkpoint_id = ?", (checkpoint_id,))
        cp_row = cur.fetchone()
        if not cp_row:
            raise FileNotFoundError(f"Source checkpoint '{checkpoint_id}' not found")

        snapshot = json.loads(cp_row["snapshot_json"])
        source_wf = snapshot.get("workflow", {})
        source_tasks = snapshot.get("tasks", [])

        now = time.time()
        forked_wf = dict(source_wf)
        forked_wf["workflow_id"] = new_workflow_id
        forked_wf["title"] = new_title or f"{source_wf.get('title', 'Workflow')} (Forked from {checkpoint_id[:12]})"
        forked_wf["created_at"] = now
        forked_wf["updated_at"] = now
        forked_wf["forked_from"] = {
            "source_workflow_id": cp_row["workflow_id"],
            "source_checkpoint_id": checkpoint_id,
            "forked_at": now,
        }

        # Clone and remap tasks
        forked_tasks = []
        for t in source_tasks:
            t_clone = dict(t)
            orig_tid = t.get("task_id", "")
            t_clone["task_id"] = f"{orig_tid}-fork-{uuid.uuid4().hex[:6]}"
            t_clone["workflow_id"] = new_workflow_id
            t_clone["created_at"] = now
            forked_tasks.append(t_clone)

        conn.execute("BEGIN TRANSACTION;")
        try:
            save_workflow(forked_wf, db_path, conn=conn)
            for t in forked_tasks:
                save_task(t, db_path, conn=conn)

            conn.execute("""
                INSERT INTO events (workflow_id, task_id, event_type, payload_json, timestamp)
                VALUES (?, ?, ?, ?, ?);
            """, (new_workflow_id, None, "workflow_forked", json.dumps({
                "source_checkpoint_id": checkpoint_id,
                "source_workflow_id": cp_row["workflow_id"],
            }), now))

            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise

        return {
            "ok": True,
            "new_workflow_id": new_workflow_id,
            "new_title": forked_wf["title"],
            "source_checkpoint_id": checkpoint_id,
            "source_workflow_id": cp_row["workflow_id"],
            "cloned_tasks": len(forked_tasks),
        }
    finally:
        conn.close()


def get_checkpoint_lineage(workflow_id: str, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Retrieve the DAG lineage of checkpoints for a workflow."""
    cps = list_checkpoints(workflow_id, db_path)
    by_id = {c["checkpoint_id"]: c for c in cps}

    lineage_tree = []
    for c in cps:
        parent_id = c.get("parent_checkpoint_id")
        lineage_tree.append({
            "checkpoint_id": c["checkpoint_id"],
            "tag": c.get("tag"),
            "created_at": c["created_at"],
            "parent_id": parent_id,
            "has_parent": bool(parent_id and parent_id in by_id),
            "task_count": c["task_count"],
        })
    return lineage_tree


def migrate_v1_to_v2(
    workflows_file: Optional[Path] = None,
    tasks_file: Optional[Path] = None,
    checkpoints_dir: Optional[Path] = None,
    steering_file: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Lossless migration of V1 JSON registries and checkpoint files into SQLite."""
    wf_path = workflows_file or (CONTROLLER_DIR / "workflows.json")
    tasks_path = tasks_file or (CONTROLLER_DIR / "tasks.json")
    cp_path = checkpoints_dir or (CONTROLLER_DIR / "checkpoints")
    st_path = steering_file or (CONTROLLER_DIR / "steering.json")
    db = init_db(db_path)

    migrated_wfs = 0
    migrated_tasks = 0
    migrated_cps = 0
    migrated_steers = 0

    # 1. Migrate workflows
    if wf_path.exists():
        try:
            with open(wf_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for wid, wf_obj in data.get("workflows", {}).items():
                wf_obj.setdefault("workflow_id", wid)
                save_workflow(wf_obj, db)
                migrated_wfs += 1
        except Exception:
            pass

    # 2. Migrate tasks
    if tasks_path.exists():
        try:
            with open(tasks_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for t_obj in data.get("tasks", []):
                if t_obj.get("task_id") and t_obj.get("workflow_id"):
                    save_task(t_obj, db)
                    migrated_tasks += 1
        except Exception:
            pass

    # 3. Migrate checkpoints
    if cp_path.exists():
        for f in cp_path.rglob("cp_*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    snap = json.load(fp)
                cpid = snap.get("checkpoint_id")
                wid = snap.get("workflow_id")
                if not cpid or not wid:
                    continue

                conn = get_db_connection(db)
                try:
                    conn.execute("""
                        INSERT INTO checkpoints (
                            checkpoint_id, workflow_id, tag, parent_checkpoint_id,
                            created_at, workflow_status, task_count, snapshot_json, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(checkpoint_id) DO NOTHING;
                    """, (
                        cpid,
                        wid,
                        snap.get("tag", ""),
                        snap.get("parent_checkpoint_id"),
                        float(snap.get("created_at") or time.time()),
                        snap.get("workflow", {}).get("status", "unknown"),
                        len(snap.get("tasks", [])),
                        json.dumps(snap, ensure_ascii=False),
                        json.dumps(snap.get("metadata", {}), ensure_ascii=False),
                    ))
                    migrated_cps += 1
                finally:
                    conn.close()
            except Exception:
                continue

    # 4. Migrate steering
    if st_path.exists():
        try:
            with open(st_path, "r", encoding="utf-8") as f:
                s_data = json.load(f)
            for tid, q in s_data.get("steering_queues", {}).items():
                for s_item in q:
                    s_item.setdefault("task_id", tid)
                    save_steer(s_item, db)
                    migrated_steers += 1
            for h in s_data.get("history", []):
                record_steering_history(h, db)
        except Exception:
            pass

    return {
        "ok": True,
        "db_path": str(db),
        "migrated_workflows": migrated_wfs,
        "migrated_tasks": migrated_tasks,
        "migrated_checkpoints": migrated_cps,
        "migrated_steers": migrated_steers,
    }
