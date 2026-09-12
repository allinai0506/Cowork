#!/opt/homebrew/bin/python3
import json
import fcntl
import time
from pathlib import Path

HOME = Path.home()
ROOT = HOME / ".herdr-controller"
POOLS_FILE = ROOT / "agent-pools.json"
PROJECTS_FILE = ROOT / "projects.json"
WORKFLOWS_FILE = ROOT / "workflows.json"
TASKS_FILE = ROOT / "tasks.json"
RESERVATIONS_FILE = ROOT / "agent-reservations.json"
ROUTER_LOCK_FILE = ROOT / "agent-router.lock"

try:
    from .projects import workflow_config_for
    from .workflow import find_node
except ImportError:
    from herdr.projects import workflow_config_for
    from herdr.workflow import find_node

DEFAULT_ALLOWED = ["opencode", "codex", "qodercli", "claude", "agy", "pi"]

DEFAULT_STAGE_PREFERENCES = {
    "requirements": ["claude", "qodercli", "opencode", "codex", "agy", "pi"],
    "plan": ["claude", "qodercli", "codex", "opencode", "agy", "pi"],
    "implementation": ["opencode", "codex", "qodercli", "agy", "claude", "pi"],
    "test": ["codex", "opencode", "qodercli", "claude", "agy", "pi"],
    "review": ["claude", "codex", "qodercli", "opencode", "agy", "pi"],
    "wrapup": ["claude", "qodercli", "opencode", "codex", "agy", "pi"],
}

DEFAULT_TASK_TYPE_PREFERENCES = {
    "feat": ["opencode", "codex", "qodercli", "agy", "claude", "pi"],
    "fix": ["opencode", "codex", "qodercli", "agy", "claude", "pi"],
    "refactor": ["codex", "opencode", "qodercli", "claude", "agy", "pi"],
    "docs": ["claude", "qodercli", "opencode", "codex", "agy", "pi"],
    "test": ["codex", "opencode", "qodercli", "claude", "agy", "pi"],
    "chore": ["codex", "opencode", "qodercli", "claude", "agy", "pi"],
    "perf": ["codex", "opencode", "qodercli", "claude", "agy", "pi"],
    "ci": ["codex", "opencode", "qodercli", "claude", "agy", "pi"],
}

def _load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)

def default_pool():
    return {
        "allowed_agents": list(DEFAULT_ALLOWED),
        "disabled_agents": [],
        "stage_preferences": {k: list(v) for k, v in DEFAULT_STAGE_PREFERENCES.items()},
        "task_type_preferences": {k: list(v) for k, v in DEFAULT_TASK_TYPE_PREFERENCES.items()},
    }

def load_pools():
    return _load(POOLS_FILE, {"projects": {}})

def save_pools(data):
    _save(POOLS_FILE, data)

def ensure_pool_for_project(project_id):
    data = load_pools()
    projects = data.setdefault("projects", {})
    if project_id not in projects:
        projects[project_id] = default_pool()
        save_pools(data)
    return projects[project_id]

def workflow_record(workflow_id):
    data = _load(WORKFLOWS_FILE, {"workflows": {}})
    return data.get("workflows", {}).get(workflow_id, {})

def set_workflow_agent_override(workflow_id, agent):
    data = _load(WORKFLOWS_FILE, {"workflows": {}})
    record = data.setdefault("workflows", {}).get(workflow_id)
    if not record:
        return
    record["agent_override"] = agent or "auto"
    _save(WORKFLOWS_FILE, data)

def _clean_reservations(data, ttl=300):
    now = time.time()
    tasks = _load(TASKS_FILE, {"tasks": []})
    registered = {
        t.get("task_id")
        for t in tasks.get("tasks", [])
        if t.get("task_id")
    }

    cleaned = {}
    for task_id, item in data.get("reservations", {}).items():
        if task_id in registered:
            continue
        created_at = float(item.get("created_at", 0) or 0)
        if created_at and (now - created_at) <= ttl:
            cleaned[task_id] = item

    data["reservations"] = cleaned
    return data


def release_agent_reservation(task_id):
    ROUTER_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(ROUTER_LOCK_FILE, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)

        data = _load(
            RESERVATIONS_FILE,
            {"reservations": {}},
        )
        data = _clean_reservations(data)
        data.setdefault("reservations", {}).pop(task_id, None)
        _save(RESERVATIONS_FILE, data)

        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _active_agent_loads(project_id):
    data = _load(TASKS_FILE, {"tasks": []})
    active = {
        "pending", "dispatched", "working", "blocked", "agent_done",
        "rework", "completed", "committed", "integrated", "cleanup_ready",
    }
    loads = {}
    for task in data.get("tasks", []):
        if task.get("project_id") != project_id:
            continue
        if task.get("status") not in active:
            continue
        agent = task.get("agent")
        if agent:
            loads[agent] = loads.get(agent, 0) + 1
    return loads


def _candidate_order(pool, stage, task_type, node_policy=None):
    node_policy = node_policy or {}
    preferred = list(node_policy.get("preferred", []))
    excluded = set(node_policy.get("exclude", []))

    ordered = []
    for agent in [
        *preferred,
        *pool.get("stage_preferences", {}).get(stage, []),
        *pool.get("task_type_preferences", {}).get(task_type, []),
        *pool.get("allowed_agents", []),
    ]:
        if agent in excluded:
            continue
        if agent not in ordered:
            ordered.append(agent)
    return ordered

def choose_agent(
    workflow_id,
    stage,
    task_type,
    requested="auto",
    reservation_key=None,
):
    record = workflow_record(workflow_id)
    project_id = record.get("project_id")

    if not project_id:
        return requested if requested and requested != "auto" else "opencode"

    node_policy = {}
    if workflow_id:
        wf_cfg = workflow_config_for(workflow_id)
        if wf_cfg:
            node = find_node(wf_cfg, stage)
            if node:
                node_policy = node.get("agent_policy", {})

    pool = ensure_pool_for_project(project_id)
    allowed = set(pool.get("allowed_agents", []))
    disabled = set(pool.get("disabled_agents", []))
    workflow_override = record.get("agent_override", "auto")

    if workflow_override and workflow_override != "auto":
        selected = workflow_override
    elif requested and requested != "auto":
        selected = requested
    elif node_policy.get("fixed"):
        selected = node_policy["fixed"]
    else:
        selected = None

    healthy = set(record.get("healthy_agents", []))

    if selected:
        if selected not in allowed:
            raise RuntimeError(
                f"Agent '{selected}' is not allowed for project {project_id}"
            )
        if selected in disabled:
            raise RuntimeError(
                f"Agent '{selected}' is disabled for project {project_id}"
            )
        if healthy and selected not in healthy:
            status = record.get("unhealthy_agents", {}).get(
                selected,
                "NOT_READY",
            )
            raise RuntimeError(
                f"Agent '{selected}' failed Workflow Deep Preflight: {status}"
            )
        return selected

    ROUTER_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(ROUTER_LOCK_FILE, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)

        reservations = _load(
            RESERVATIONS_FILE,
            {"reservations": {}},
        )
        reservations = _clean_reservations(reservations)

        active_loads = _active_agent_loads(project_id)
        reserved_loads = {}

        for item in reservations.get("reservations", {}).values():
            if item.get("project_id") != project_id:
                continue
            agent = item.get("agent")
            if agent:
                reserved_loads[agent] = reserved_loads.get(agent, 0) + 1

        candidates = [
            agent
            for agent in _candidate_order(pool, stage, task_type, node_policy)
            if (
                agent in allowed
                and agent not in disabled
                and (not healthy or agent in healthy)
            )
        ]

        if not candidates:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            raise RuntimeError(
                f"No enabled Agent available for project {project_id}"
            )

        ranked = sorted(
            enumerate(candidates),
            key=lambda item: (
                active_loads.get(item[1], 0)
                + reserved_loads.get(item[1], 0),
                item[0],
            ),
        )

        selected = ranked[0][1]

        if reservation_key:
            reservations.setdefault("reservations", {})[reservation_key] = {
                "project_id": project_id,
                "workflow_id": workflow_id,
                "stage": stage,
                "task_type": task_type,
                "agent": selected,
                "created_at": time.time(),
            }
            _save(RESERVATIONS_FILE, reservations)

        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    return selected


def describe_project_pool(project_id):
    return {"project_id": project_id, **ensure_pool_for_project(project_id)}

def initialize_registered_projects():
    projects = _load(PROJECTS_FILE, {"projects": {}})
    for project in projects.get("projects", {}).values():
        project_id = project.get("project_id")
        if project_id:
            ensure_pool_for_project(project_id)

if __name__ == "__main__":
    initialize_registered_projects()
    print(json.dumps(load_pools(), ensure_ascii=False, indent=2))
