#!/usr/bin/env python3
"""Task Archive Query (herdr/archive.py).

Functional core for the console archive/query list: presentation-only
filtering, sorting and pagination over task records supplied by the caller.
Persistence and I/O stay in the imperative shell (console / StateStore).
"""

from typing import Any, Dict, List, Optional

ARCHIVED_STATUSES = ("cleaned", "superseded", "failed")

ACTIVE_STATUSES = (
    "pending",
    "dispatched",
    "working",
    "blocked",
    "agent_done",
    "rework",
    "paused",
    "interrupted",
)

STATUS_GROUPS: Dict[str, Optional[tuple]] = {
    "archived": ARCHIVED_STATUSES,
    "active": ACTIVE_STATUSES,
    "all": None,
}

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

_SEARCH_FIELDS = (
    "task_id",
    "goal",
    "workflow_id",
    "project_name",
    "node",
    "node_label",
    "stage",
    "stage_label",
)


def _norm(value: Any) -> str:
    return str(value).strip().lower() if value is not None else ""


def _recent(task: Dict[str, Any]) -> float:
    raw = (
        task.get("updated_at")
        or task.get("last_activity_at")
        or task.get("created_at")
        or 0
    )
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _to_seconds(raw: Any) -> Optional[float]:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _matches(
    task: Dict[str, Any],
    project_id: str,
    workflow_id: str,
    agent: str,
    statuses: Optional[tuple],
    keyword: str,
) -> bool:
    if project_id and task.get("project_id") != project_id:
        return False
    if workflow_id and workflow_id not in _norm(task.get("workflow_id")):
        return False
    if agent and task.get("agent") != agent:
        return False
    if statuses is not None and task.get("status") not in statuses:
        return False
    if keyword:
        haystack = " ".join(_norm(task.get(field)) for field in _SEARCH_FIELDS)
        if keyword not in haystack:
            return False
    return True


def summarize_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """Compact projection of a task record for archive list rendering."""
    created = task.get("created_at")
    updated = task.get("updated_at") or task.get("last_activity_at") or created or 0
    created_s = _to_seconds(created)
    updated_s = _to_seconds(updated)
    duration = None
    if created_s is not None and updated_s is not None:
        duration = max(0.0, updated_s - created_s)
    node = task.get("node") or task.get("stage")
    return {
        "task_id": task.get("task_id"),
        "workflow_id": task.get("workflow_id"),
        "project_id": task.get("project_id"),
        "project_name": task.get("project_name"),
        "node": node,
        "node_label": task.get("node_label") or task.get("stage_label") or node,
        "agent": task.get("agent"),
        "status": task.get("status"),
        "stage_verdict": task.get("stage_verdict") or "",
        "goal": task.get("goal") or "",
        "last_result": task.get("last_result") or "",
        "superseded_by": task.get("superseded_by") or "",
        "created_at": created,
        "updated_at": updated,
        "duration_seconds": duration,
    }


def query_archived_tasks(
    tasks: List[Dict[str, Any]],
    project_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
    agent: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> Dict[str, Any]:
    """Filter, sort (most recently updated first) and paginate task records.

    status accepts a group alias ("archived" / "active" / "all") or an exact
    status name; empty/None defaults to the "archived" group.
    """
    status_key = _norm(status) or "archived"
    if status_key in STATUS_GROUPS:
        statuses = STATUS_GROUPS[status_key]
    else:
        statuses = (status_key,)

    project_key = str(project_id).strip() if project_id else ""
    workflow_key = _norm(workflow_id)
    agent_key = str(agent).strip() if agent else ""
    keyword = _norm(q)

    matched = [
        task
        for task in tasks or []
        if _matches(task, project_key, workflow_key, agent_key, statuses, keyword)
    ]
    matched.sort(key=lambda t: (-_recent(t), str(t.get("task_id") or "")))

    try:
        offset_i = max(0, int(offset))
    except (TypeError, ValueError):
        offset_i = 0
    try:
        limit_i = int(limit)
    except (TypeError, ValueError):
        limit_i = DEFAULT_LIMIT
    limit_i = max(1, min(MAX_LIMIT, limit_i))

    page = matched[offset_i : offset_i + limit_i]
    return {
        "total": len(matched),
        "count": len(page),
        "limit": limit_i,
        "offset": offset_i,
        "status": status_key,
        "items": [summarize_task(task) for task in page],
    }
