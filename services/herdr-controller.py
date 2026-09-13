#!/usr/bin/env python3

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import queue
import socket
import subprocess
import threading
import time

import sys
HERDR_ROOT = Path(__file__).resolve().parent.parent
if str(HERDR_ROOT) not in sys.path:
    sys.path.insert(0, str(HERDR_ROOT))

SOCKET_PATH = os.path.expanduser("~/.config/herdr/herdr.sock")
TASKS_FILE = os.path.expanduser("~/.herdr-controller/tasks.json")
_task_bin = HERDR_ROOT / "bin" / "herdr-task"
TASK_MANAGER = str(_task_bin) if _task_bin.exists() else os.path.expanduser("~/herdr/bin/herdr-task")
try:
    from herdr.projects import project_for_workflow, workflow_config_for
    from herdr.workflow import find_node, get_ready_nodes, is_workflow_completed, normalize_workflow
except ImportError:
    from herdr_projects import project_for_workflow, workflow_config_for
    from herdr_workflow import find_node, get_ready_nodes, is_workflow_completed, normalize_workflow

STAGE_STATE_FILE = os.path.expanduser(
    "~/.herdr-controller/stage-state.json"
)

STAGE_POLICIES_FILE = os.path.expanduser(
    "~/.herdr-controller/stage-policies.json"
)

COORDINATOR_PANE = "w6:p1H"

WORKFLOWS_FILE = os.path.expanduser("~/.herdr-controller/workflows.json")

# 已触发过 close-workflow 的 workflow,防止轮询期间重复派发。
_workflow_close_inflight = set()


def maybe_close_completed_workflow(workflow_id):
    """Workflow 全部节点完成后,自动执行物理收尾(关 pane/删 clone/归档)。

    close-workflow 自带幂等与终态闸门;这里只负责防重入派发。
    """
    if not workflow_id or workflow_id in _workflow_close_inflight:
        return
    try:
        with open(WORKFLOWS_FILE, "r", encoding="utf-8") as f:
            entry = json.load(f).get("workflows", {}).get(workflow_id) or {}
        if entry.get("status") == "completed":
            return
        # Fix-loop reopen 闩:重开后的 workflow 在首个任务进入 ACTIVE
        # 之前,旧任务仍全为完成系,必须挡住 sweep 的自消除 close。
        if entry.get("suppress_auto_close"):
            return
    except (OSError, json.JSONDecodeError):
        pass

    _workflow_close_inflight.add(workflow_id)

    def _run():
        try:
            result = subprocess.run(
                [TASK_MANAGER, "close-workflow", workflow_id],
                text=True,
                capture_output=True,
                timeout=900,
            )
            if result.stdout.strip():
                print(result.stdout.strip())
            if result.returncode != 0:
                print(
                    f"[WORKFLOW CLOSE ERROR] "
                    f"workflow={workflow_id}: "
                    f"{result.stderr.strip() or result.stdout.strip()}"
                )
        finally:
            _workflow_close_inflight.discard(workflow_id)

    threading.Thread(
        target=_run,
        daemon=True,
        name=f"wf-close-{workflow_id}",
    ).start()


# ============================================================
# Gate verdicts & fix-loop
# ============================================================

# 常见门禁阶段的内置默认;显式配置(workflow 节点 gate / stage-policies.json)
# 优先于这里。verdict 缺失(lenient)时门禁不生效,存量 workflow 行为不变。
GATE_DEFAULTS = {
    "test": {"retry_node": "implementation"},
    "review": {"retry_node": "implementation"},
    "wrapup": {"retry_node": "implementation"},
}

FIX_LOOP_MAX = int(os.environ.get("HERDR_FIX_LOOP_MAX", "3"))

# 可作废状态集合,必须与 bin/herdr-task TRANSITIONS 中
# 允许 → superseded 的状态保持一致(pending/committed/integrated 除外)。
FIX_LOOP_SUPERSEDEABLE = {
    "dispatched",
    "working",
    "blocked",
    "agent_done",
    "rework",
    "cleaned",
    "failed",
}


def resolve_gate_config(node, node_id):
    gate = (node or {}).get("gate") or get_stage_policy(node_id).get("gate")

    if gate is None:
        gate = GATE_DEFAULTS.get(node_id)

    if not gate:
        return None

    return {
        "retry_node": gate.get("retry_node", "implementation"),
        "max_loops": int(gate.get("max_loops", FIX_LOOP_MAX)),
    }


def gate_verdict(workflow_id, node_id):
    """Fail-safe 门禁结论:任一未作废任务的 blocked 结论即 blocked。"""
    verdict = None

    for task in load_tasks():
        if task.get("workflow_id") != workflow_id:
            continue
        if node_id not in (task.get("node"), task.get("stage")):
            continue
        if task.get("status") == "superseded":
            continue

        task_verdict = task.get("stage_verdict")

        if task_verdict == "blocked":
            return "blocked"
        if task_verdict == "pass":
            verdict = "pass"

    return verdict


def latest_branch_for_node(workflow_id, node_id):
    best = None

    for task in load_tasks():
        if task.get("workflow_id") != workflow_id:
            continue
        if node_id not in (task.get("node"), task.get("stage")):
            continue
        if not task.get("branch"):
            continue
        if best is None or task.get("updated_at", 0) > best.get("updated_at", 0):
            best = task

    return best.get("branch") if best else None


def _collect_downstream_nodes(nodes_by_id, root_id):
    """root 节点自身 + 传递闭包的全部下游节点。"""
    dependents = {}

    for node in nodes_by_id.values():
        for dep in node.get("depends_on", []):
            dependents.setdefault(dep, set()).add(node["id"])

    seen = {root_id}
    frontier = [root_id]

    while frontier:
        current = frontier.pop()
        for nxt in dependents.get(current, ()):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)

    return seen


def invalidate_for_fix_loop(workflow_id, gate_node_id, workflow_cfg):
    """作废 gate 节点及其全部下游的非 superseded 任务(fix-loop 回流前提)。

    completed/cleanup_ready 中间态先 finalize 规范化到 cleaned——
    completed→superseded 会被状态机拒绝;pending 不可作废,跳过。
    """
    nodes_by_id = {
        n["id"]: n for n in workflow_cfg.get("nodes", [])
    }
    node_ids = _collect_downstream_nodes(nodes_by_id, gate_node_id)

    supersedeable = FIX_LOOP_SUPERSEDEABLE
    invalidated = []

    for task in load_tasks():
        if task.get("workflow_id") != workflow_id:
            continue
        if (
            task.get("node") not in node_ids
            and task.get("stage") not in node_ids
        ):
            continue

        status = task.get("status")
        task_id = task["task_id"]

        if status == "superseded":
            continue

        if status in ("completed", "cleanup_ready"):
            result = subprocess.run(
                [TASK_MANAGER, "finalize", task_id],
                text=True,
                capture_output=True,
            )
            if result.returncode != 0:
                print(
                    f"[FIX LOOP INVALIDATE ERROR] finalize {task_id}: "
                    f"{result.stderr.strip() or result.stdout.strip()}"
                )
                continue
            status = (get_task(task_id) or {}).get("status")

        if status not in supersedeable:
            print(
                f"[FIX LOOP INVALIDATE SKIP] task={task_id} "
                f"status={status} cannot be superseded"
            )
            continue

        result = subprocess.run(
            [
                TASK_MANAGER, "supersede", task_id,
                "--reason", f"fix-loop: gate {gate_node_id} blocked",
            ],
            text=True,
            capture_output=True,
        )

        if result.returncode == 0:
            invalidated.append(task_id)
        else:
            print(
                f"[FIX LOOP INVALIDATE ERROR] supersede {task_id}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

    return invalidated


def _bump_fix_loop_count(workflow_id, retry_node):
    state = load_stage_state()
    key = f"{workflow_id}|fixloop|{retry_node}"
    count = int(state.get(key, 0)) + 1
    state[key] = count
    save_stage_state(state)
    return count


def blocked_gate_dependency(workflow_id, ready_node, workflow_cfg):
    """ready_node 的依赖中是否存在 verdict=blocked 的门禁节点。"""
    nodes_by_id = {
        n["id"]: n for n in workflow_cfg.get("nodes", [])
    }

    for dep in ready_node.get("depends_on", []):
        gate_cfg = resolve_gate_config(nodes_by_id.get(dep), dep)

        if not gate_cfg:
            continue

        if gate_verdict(workflow_id, dep) == "blocked":
            return dep, gate_cfg

    return None


def handle_fix_loop(workflow_id, gate_node_id, gate_cfg, workflow_cfg):
    """原子作废 + 计数 + 投递 fix_loop 事件;幂等(无作废即不重发)。"""
    retry_node = gate_cfg.get("retry_node", "implementation")

    blockers = []

    for task in load_tasks():
        if task.get("workflow_id") != workflow_id:
            continue
        if gate_node_id not in (task.get("node"), task.get("stage")):
            continue
        if task.get("status") == "superseded":
            continue
        if task.get("stage_verdict") == "blocked":
            blockers.append(
                {
                    "task_id": task.get("task_id"),
                    "note": task.get("stage_verdict_note", ""),
                }
            )

    invalidated = invalidate_for_fix_loop(
        workflow_id, gate_node_id, workflow_cfg
    )

    if not invalidated:
        return

    loop_count = _bump_fix_loop_count(workflow_id, retry_node)

    coordinator_queue.put(
        {
            "kind": "fix_loop",
            "workflow_id": workflow_id,
            "gate_stage": gate_node_id,
            "retry_node": retry_node,
            "blockers": blockers,
            "invalidated": invalidated,
            "loop_count": loop_count,
            "max_loops": gate_cfg.get("max_loops", FIX_LOOP_MAX),
            "suggested_branch": latest_branch_for_node(
                workflow_id, retry_node
            ),
        }
    )

    print(
        f"[FIX LOOP QUEUED] "
        f"workflow={workflow_id} "
        f"gate={gate_node_id} "
        f"retry={retry_node} "
        f"loop={loop_count} "
        f"invalidated={len(invalidated)}"
    )


def coordinator_pane_for_workflow(workflow_id=None):
    if workflow_id:
        project = project_for_workflow(workflow_id) or {}
        pane = project.get("coordinator_pane_id")
        if pane:
            return pane

    return None

listeners = {}
task_sockets = {}

lock = threading.Lock()

coordinator_queue = queue.Queue()
queued_events = set()

# Per-workflow concurrency: each workflow gets its own execution slot so that
# a blocked coordinator for workflow A cannot stall dispatch for workflow B.
_wf_dispatch_locks: dict = {}
_wf_dispatch_locks_meta = threading.Lock()
_coordinator_executor = ThreadPoolExecutor(
    max_workers=16,
    thread_name_prefix="coord-worker"
)


def _workflow_dispatch_lock(workflow_id: str) -> threading.Lock:
    """Return the per-workflow serialization lock (created on first use)."""
    with _wf_dispatch_locks_meta:
        if workflow_id not in _wf_dispatch_locks:
            _wf_dispatch_locks[workflow_id] = threading.Lock()
        return _wf_dispatch_locks[workflow_id]


# ============================================================
# Registry
# ============================================================

def load_tasks():
    with open(TASKS_FILE, "r", encoding="utf-8") as f:
        return json.load(f).get("tasks", [])


def get_task(task_id):
    for task in load_tasks():
        if task["task_id"] == task_id:
            return task
    return None


def set_task_status(task_id, status):
    result = subprocess.run(
        [
            TASK_MANAGER,
            "set",
            task_id,
            status
        ],
        text=True,
        capture_output=True
    )

    if result.returncode != 0:
        print(
            f"[STATE ERROR] {task_id}: "
            f"{result.stdout.strip() or result.stderr.strip()}"
        )
        return False

    print(
        f"[STATE] "
        f"{result.stdout.strip()}"
    )

    return True


# ============================================================
# Stage policies
# ============================================================

def load_stage_policies():
    try:
        with open(
            STAGE_POLICIES_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            return json.load(f)

    except Exception as e:
        print(
            f"[STAGE POLICY ERROR] {e}"
        )
        return {}


def get_stage_policy(stage_key):
    policies = load_stage_policies()

    return policies.get(
        stage_key,
        {}
    )



# ============================================================
# Workflow stage state
# ============================================================

def load_stage_state():
    if not os.path.exists(STAGE_STATE_FILE):
        return {}

    try:
        with open(
            STAGE_STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            return json.load(f)
    except Exception:
        return {}


def save_stage_state(data):
    tmp = STAGE_STATE_FILE + ".tmp"

    with open(
        tmp,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(
        tmp,
        STAGE_STATE_FILE
    )


def stage_advance_key(
    workflow_id,
    stage
):
    return f"{workflow_id}:{stage}"


def mark_stage_advance_queued(
    workflow_id,
    stage
):
    key = stage_advance_key(
        workflow_id,
        stage
    )

    with lock:
        state = load_stage_state()

        if state.get(key) in (
            "queued",
            "notified"
        ):
            return False

        state[key] = "queued"
        save_stage_state(state)

    return True


def mark_stage_advance_notified(
    workflow_id,
    stage
):
    key = stage_advance_key(
        workflow_id,
        stage
    )

    with lock:
        state = load_stage_state()
        state[key] = "notified"
        save_stage_state(state)


def clear_stage_advance(
    workflow_id,
    stage
):
    key = stage_advance_key(
        workflow_id,
        stage
    )

    with lock:
        state = load_stage_state()
        state.pop(key, None)
        save_stage_state(state)
def reset_queued_stage_states():
    with lock:
        state = load_stage_state()
        changed = False
        for k in list(state.keys()):
            if state[k] == "queued":
                del state[k]
                changed = True
        if changed:
            save_stage_state(state)



# ============================================================
# Coordinator queue
# ============================================================

def get_stage_status(
    workflow_id,
    stage
):
    try:
        output = subprocess.check_output(
            [
                TASK_MANAGER,
                "stage-status",
                workflow_id,
                stage
            ],
            text=True
        )

        return json.loads(output)

    except Exception as e:
        print(
            f"[STAGE STATUS ERROR] "
            f"workflow={workflow_id} "
            f"stage={stage}: {e}"
        )
        return None


def is_node_complete(workflow_id, node_id):
    tasks = [
        t for t in load_tasks()
        if t.get("workflow_id") == workflow_id
        and (t.get("node") == node_id or t.get("stage") == node_id)
    ]
    if not tasks:
        return False

    # Superseded tasks are excluded from completion calculation — they were
    # replaced by another task whose outcome is the authoritative result.
    active = [
        t for t in tasks
        if t.get("status") != "superseded" and not t.get("superseded_by")
    ]

    # A node with only superseded tasks and no replacements is incomplete.
    if not active:
        return False

    return all(
        t.get("status") in (
            "completed", "committed", "integrated", "cleanup_ready", "cleaned"
        )
        for t in active
    )


def reconcile_stage_advance_states(workflow_id, workflow_cfg):
    """Revoke 'notified' stage-state entries when their predecessor nodes
    have regressed (e.g., a task failed or was superseded with no replacement).

    Without this, a stage whose predecessor regresses after the coordinator
    was already notified would never be re-triggered — because
    mark_stage_advance_queued returns False for 'notified' entries and the
    node never appears in get_ready_nodes again.
    """
    with lock:
        state = load_stage_state()
        changed = False

        nodes_by_id = {
            n["id"]: n
            for n in workflow_cfg.get("nodes", [])
        }

        for key in list(state.keys()):
            if not key.startswith(f"{workflow_id}:"):
                continue
            if state[key] != "notified":
                continue

            node_id = key.split(":", 1)[1]
            node = nodes_by_id.get(node_id)
            if not node:
                continue

            # Check whether all predecessors are still complete.
            deps = node.get("depends_on", [])
            predecessors_complete = all(
                is_node_complete(workflow_id, dep)
                for dep in deps
            )
            if not predecessors_complete:
                del state[key]
                changed = True
                print(
                    f"[STAGE REVOKE] "
                    f"workflow={workflow_id} node={node_id}: "
                    "predecessors no longer complete, revoking 'notified' lock"
                )

        if changed:
            save_stage_state(state)


def enqueue_stage_advance(task):
    workflow_id = task.get("workflow_id")
    if workflow_id:
        check_workflow_stage_advance(workflow_id)


def check_workflow_stage_advance(workflow_id):
    if not workflow_id:
        return

    pane = coordinator_pane_for_workflow(workflow_id)
    if not pane:
        return

    workflow_cfg = workflow_config_for(workflow_id)
    if not workflow_cfg:
        return

    workflow_record = project_for_workflow(workflow_id) or {}
    # New factory starts persist the requirement and keep this gate closed
    # until Deep Preflight has completed. This prevents the controller from
    # delivering a stage event before the startup request is ready.
    if workflow_record.get("startup_ready") is False:
        print(f"[STARTUP WAIT] workflow={workflow_id} preflight/request not ready")
        return

    if workflow_cfg.get("nodes"):
        # Revoke stale 'notified' locks before computing ready nodes,
        # so that regressed stages can be re-triggered.
        reconcile_stage_advance_states(workflow_id, workflow_cfg)

        completed_nodes = {
            n["id"]
            for n in workflow_cfg.get("nodes", [])
            if is_node_complete(workflow_id, n["id"])
        }

        if is_workflow_completed(workflow_cfg, completed_nodes):
            # 交付终态门禁:任一门禁节点 verdict=blocked 时不得关闭,
            # 回流 fix-loop(由 handle_fix_loop 原子作废并派发事件)。
            nodes_by_id = {
                n["id"]: n for n in workflow_cfg.get("nodes", [])
            }
            blocked_gates = []

            for node_id in sorted(completed_nodes):
                gate_cfg = resolve_gate_config(
                    nodes_by_id.get(node_id), node_id
                )
                if (
                    gate_cfg
                    and gate_verdict(workflow_id, node_id) == "blocked"
                ):
                    blocked_gates.append((node_id, gate_cfg))

            if blocked_gates:
                for gate_node_id, gate_cfg in blocked_gates:
                    handle_fix_loop(
                        workflow_id, gate_node_id, gate_cfg, workflow_cfg
                    )
                return

            print(
                f"[WORKFLOW COMPLETE] "
                f"workflow={workflow_id}"
            )
            maybe_close_completed_workflow(workflow_id)
            return

        ready_nodes = get_ready_nodes(workflow_cfg, completed_nodes)
        for ready_node in ready_nodes:
            blocked_dep = blocked_gate_dependency(
                workflow_id, ready_node, workflow_cfg
            )
            if blocked_dep:
                gate_node_id, gate_cfg = blocked_dep
                handle_fix_loop(
                    workflow_id, gate_node_id, gate_cfg, workflow_cfg
                )
                continue

            ready_id = ready_node["id"]
            if not mark_stage_advance_queued(workflow_id, ready_id):
                continue

            deps = ready_node.get("depends_on", [])
            source_stage = deps[-1] if deps else "start"

            coordinator_queue.put(
                {
                    "kind": "stage_advance",
                    "workflow_id": workflow_id,
                    "stage": source_stage,
                    "node_id": ready_id,
                    "next_stage": ready_id,
                    "stage_label": ready_node.get("label", ready_id),
                    "node": ready_node,
                }
            )

            print(
                f"[STAGE ADVANCE QUEUED] "
                f"workflow={workflow_id} "
                f"{source_stage} -> {ready_id}"
            )
        return

    # Fallback to legacy single-step stage advance
    for stage in workflow_cfg.get("stages", []):
        stage_key = stage.get("key") or stage.get("id")
        if not is_node_complete(workflow_id, stage_key):
            continue

        next_stage = stage.get("next")
        if not next_stage:
            continue

        gate_cfg = resolve_gate_config(None, stage_key)
        if gate_cfg and gate_verdict(workflow_id, stage_key) == "blocked":
            handle_fix_loop(workflow_id, stage_key, gate_cfg, workflow_cfg)
            continue

        if not mark_stage_advance_queued(workflow_id, next_stage):
            continue

        coordinator_queue.put(
            {
                "kind": "stage_advance",
                "workflow_id": workflow_id,
                "stage": stage_key,
                "next_stage": next_stage,
                "stage_label": stage.get("label", stage_key),
            }
        )

        print(
            f"[STAGE ADVANCE QUEUED] "
            f"workflow={workflow_id} "
            f"{stage_key} -> {next_stage}"
        )


def active_registered_workflows():
    workflows = set()
    wf_path = Path(os.path.expanduser("~/.herdr-controller/workflows.json"))
    if wf_path.exists():
        try:
            data = json.loads(wf_path.read_text(encoding="utf-8"))
            workflows.update(data.get("workflows", {}).keys())
        except Exception:
            pass

    proj_path = Path(os.path.expanduser("~/.herdr-controller/projects.json"))
    if proj_path.exists():
        try:
            p_data = json.loads(proj_path.read_text(encoding="utf-8"))
            for p in p_data.get("projects", {}).values():
                wf = p.get("workflow_id")
                if wf:
                    workflows.add(wf)
        except Exception:
            pass

    return workflows


def check_all_workflows_stage_advance():
    for wf in active_registered_workflows():
        try:
            check_workflow_stage_advance(wf)
        except Exception as e:
            print(f"[ADVANCE CHECK ERROR] workflow={wf}: {e}")



def coordinator_status(workflow_id=None):
    pane_id = coordinator_pane_for_workflow(
        workflow_id
    )

    if not pane_id:
        return "unknown"

    try:
        output = subprocess.check_output(
            [
                "herdr",
                "agent",
                "get",
                pane_id
            ],
            text=True
        )

        data = json.loads(output)

        return (
            data["result"]["agent"]
            .get("agent_status", "unknown")
        )

    except Exception as e:
        print(
            f"[COORDINATOR STATUS ERROR] "
            f"workflow={workflow_id} "
            f"pane={pane_id}: {e}"
        )
        return "unknown"

def build_coordinator_message(task, event_type):
    workflow_id = task.get("workflow_id", "unknown")
    task_id = task["task_id"]

    goal = task.get("goal", "未定义")

    criteria = "\n".join(
        f"- {item}"
        for item in task.get(
            "acceptance_criteria",
            []
        )
    )

    if not criteria:
        criteria = "- 未定义"

    if event_type == "blocked":
        return f"""
HERDR_CONTROLLER_BLOCKED_EVENT

workflow_id: {workflow_id}
task_id: {task_id}
stage: {task['stage']}
pane_id: {task['pane_id']}
agent: {task['agent']}
agent_status: blocked

任务目标：
{goal}

当前 Task 被 Agent 阻塞。

你现在只负责解除阻塞，不允许验收任务。

必须执行：

1. 使用 Herdr 读取 {task['pane_id']} 当前界面和最新输出。
2. 判断 blocked 的真实原因。
3. 如果属于低风险、当前任务范围内的正常操作，可以处理审批并让 Agent 继续。
4. 如果属于高风险操作，不得自动批准，向用户报告风险并保持 blocked。
5. blocked 阶段不得把 Task 设置为 completed。
6. blocked 阶段不得进行正式任务验收。
7. Agent 恢复执行后，Controller 会自动处理 working 状态。
8. 不要推进阶段。

blocked 只表示等待处理，不代表任务结束。
""".strip()

    if event_type == "done":
        return f"""
HERDR_CONTROLLER_DONE_EVENT

workflow_id: {workflow_id}
task_id: {task_id}
stage: {task['stage']}
pane_id: {task['pane_id']}
agent: {task['agent']}
agent_status: done

任务目标：
{goal}

验收标准：
{criteria}

Agent 本轮执行已经结束。

现在执行正式验收：

1. 使用 Herdr 读取 {task['pane_id']} 的最终输出。

2. 必须执行：
   ~/herdr/bin/herdr-task verify-baseline {task_id}

3. `verify-baseline` 是判断当前 Task 文件变化的唯一事实来源：

   - `BASELINE_MATCH`
     表示 Agent 相对于 Task 创建时没有产生新的文件变化。

   - `TASK_CHANGED`
     后面列出的文件，才是当前 Task 真正产生的变化。

4. 禁止使用普通 `git status` 判断“Agent 是否修改了文件”，
   因为 CoW Clone 会继承 Task 创建前已经存在的工作区修改。

5. 如果验收标准要求“不得修改任何文件”，必须得到：
   `BASELINE_MATCH`

6. 如果任务允许修改代码，只检查 `TASK_CHANGED` 中列出的变化
   是否符合当前 Task 的目标和范围。

7. 根据任务目标和验收标准逐项验证。

8. Agent done 不等于 Task completed。

如果验收通过：

~/herdr/bin/herdr-task set {task_id} completed --verdict pass

如果质量门结论为不通过(评审不通过、验收标准未达成等),
禁止伪造成 pass,必须如实落盘 blocker 清单:

~/herdr/bin/herdr-task set {task_id} completed --verdict blocked --note "<blocker 清单与修复指引>"

如果需要返工：

~/herdr/bin/herdr-task set {task_id} rework

然后立即重新派发明确的返工任务。

如果任务无法恢复：

~/herdr/bin/herdr-task set {task_id} failed

阶段推进前必须执行：

~/herdr/bin/herdr-task list --workflow-id {workflow_id}

只能检查当前 workflow_id 下的任务。

禁止使用其他 Workflow 或历史 Task 判断当前阶段门禁。

只有当前 Workflow 当前阶段所有必要 Task 都 completed，
才能进入下一阶段。
""".strip()

    return None


def enqueue_coordinator_event(task, event_type):
    key = f"{task['task_id']}:{event_type}"

    with lock:
        if key in queued_events:
            print(
                f"[QUEUE DUPLICATE SKIPPED] {key}"
            )
            return

        queued_events.add(key)

    coordinator_queue.put(
        {
            "task_id": task["task_id"],
            "event_type": event_type,
            "key": key
        }
    )

    print(
        f"[QUEUE] "
        f"task={task['task_id']} "
        f"event={event_type}"
    )


def wait_for_coordinator_decision(task_id, timeout=30):
    deadline = time.time() + timeout

    while time.time() < deadline:
        task = get_task(task_id)

        if not task:
            print(
                f"[DECISION ERROR] "
                f"task={task_id} missing"
            )
            return None

        status = task.get("status")

        if status != "agent_done":
            print(
                f"[DECISION] "
                f"task={task_id} "
                f"status={status}"
            )
            return status

        time.sleep(0.5)

    print(
        f"[DECISION TIMEOUT] "
        f"task={task_id} "
        f"still=agent_done"
    )

    return "agent_done"


def retry_coordinator_decision(task_id):
    task = get_task(task_id)

    if not task:
        print(
            f"[RETRY ERROR] "
            f"task={task_id} missing"
        )
        return None

    if task.get("status") != "agent_done":
        return task.get("status")

    message = f"""
HERDR_CONTROLLER_RETRY_EVENT

workflow_id: {task.get('workflow_id', 'unknown')}
task_id: {task_id}
stage: {task.get('stage', 'unknown')}
pane_id: {task.get('pane_id', 'unknown')}
agent: {task.get('agent', 'unknown')}

这是一次自动重试。

该 Task 仍停留在 agent_done，
说明上一次验收通知没有完成状态落盘。

请立即只处理这个已有 Task，不要创建新 Task：

1. 读取 Task Registry。
2. 读取 Agent 最终输出。
3. 执行：
   ~/herdr/bin/herdr-task verify-baseline {task_id}
4. 根据任务目标和验收标准完成正式验收。
5. 必须将 Task 状态更新为以下之一：
   - completed（门禁阶段必须带 --verdict pass|blocked，blocked 另附 --note）
   - rework
   - failed
6. 不要只输出文字报告而不更新 Task Registry。
""".strip()

    print(
        f"[COORDINATOR RETRY] "
        f"task={task_id}"
    )

    result = subprocess.run(
        [
            "herdr",
            "agent",
            "prompt",
            coordinator_pane_for_workflow(task.get("workflow_id")),
            message,
            "--wait",
            "--timeout",
            "120000"
        ],
        text=True,
        capture_output=True
    )

    if result.returncode != 0:
        print(
            f"[COORDINATOR RETRY ERROR] "
            f"task={task_id}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )

    return wait_for_coordinator_decision(
        task_id,
        timeout=30
    )

def finalize_completed_task(task_id):
    task = get_task(task_id)

    if not task:
        print(f"[FINALIZE SKIP] task={task_id} missing")
        return

    if task.get("status") != "completed":
        print(
            f"[FINALIZE SKIP] "
            f"task={task_id} "
            f"status={task.get('status')}"
        )
        return

    mode = task.get(
        "integration_mode",
        "none"
    )

    print(
        f"[FINALIZE] "
        f"task={task_id} "
        f"integration_mode={mode}"
    )

    # --------------------------------
    # 需要 Git 集成
    # --------------------------------
    if mode == "git":

        # 1. 将 Task 自己产生的修改安全提交
        result = subprocess.run(
            [
                TASK_MANAGER,
                "commit",
                task_id,
                "--message",
                f"task: {task_id}"
            ],
            text=True,
            capture_output=True
        )

        if result.stdout.strip():
            print(result.stdout.strip())

        if result.returncode != 0:
            print(
                f"[COMMIT ERROR] "
                f"task={task_id}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
            return

        task = get_task(task_id)

        if not task or task.get("status") != "committed":
            print(
                f"[FINALIZE ERROR] "
                f"task={task_id} "
                f"did not reach committed"
            )
            return

        # 2. Rebase + 导入主仓库 + Integration Branch
        result = subprocess.run(
            [
                TASK_MANAGER,
                "integrate",
                task_id
            ],
            text=True,
            capture_output=True
        )

        if result.stdout.strip():
            print(result.stdout.strip())

        if result.returncode != 0:
            print(
                f"[INTEGRATE ERROR] "
                f"task={task_id}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
            return

        task = get_task(task_id)

        if not task or task.get("status") != "integrated":
            print(
                f"[FINALIZE ERROR] "
                f"task={task_id} "
                f"did not reach integrated"
            )
            return

        # 3. 允许清理
        if not set_task_status(
            task_id,
            "cleanup_ready"
        ):
            return

    # --------------------------------
    # 不需要 Git 集成
    # --------------------------------
    elif mode == "none":
        if not set_task_status(
            task_id,
            "cleanup_ready"
        ):
            return

    else:
        print(
            f"[FINALIZE ERROR] "
            f"task={task_id} "
            f"unknown integration_mode={mode}"
        )
        return

    # --------------------------------
    # 自动 Cleanup
    # --------------------------------
    result = subprocess.run(
        [
            TASK_MANAGER,
            "cleanup",
            task_id
        ],
        text=True,
        capture_output=True
    )

    if result.stdout.strip():
        print(result.stdout.strip())

    if result.returncode != 0:
        print(
            f"[CLEANUP ERROR] "
            f"task={task_id}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
        return

    print(
        f"[FINALIZED] task={task_id}"
    )

    # Task 最终 cleaned 后检查整个阶段是否已经完成。
    task = get_task(task_id)

    if task:
        enqueue_stage_advance(
            task
        )


def coordinator_worker():
    """Main dispatcher: reads items from coordinator_queue and fans them out
    to per-workflow executor threads, guaranteeing at-most-one concurrent
    dispatch per workflow without blocking the queue for other workflows.
    """
    while True:
        item = coordinator_queue.get()
        try:
            # Resolve workflow_id for routing.
            workflow_id = item.get("workflow_id")
            if not workflow_id and "task_id" in item:
                t = get_task(item["task_id"])
                workflow_id = (t or {}).get("workflow_id", "unknown")

            wf_lock = _workflow_dispatch_lock(workflow_id or "unknown")
            _coordinator_executor.submit(
                _process_coordinator_item, item, wf_lock
            )
        finally:
            coordinator_queue.task_done()


def _process_coordinator_item(item, wf_lock):
    """Process one coordinator queue item inside the executor thread pool,
    serialized per-workflow via wf_lock.
    """
    with wf_lock:
        _handle_coordinator_item(item)


def _handle_coordinator_item(item):
    """Actual item handling logic (stage_advance or normal task event)."""
    # ==============================================
    # Fix Loop (gate verdict blocked)
    # ==============================================
    if item.get("kind") == "fix_loop":
        _handle_fix_loop_item(item)
        return

    # ==============================================
    # Workflow Stage Advance
    # ==============================================
    if item.get("kind") == "stage_advance":
        workflow_id = item["workflow_id"]
        coord_pane = coordinator_pane_for_workflow(workflow_id)
        if not coord_pane:
            print(
                f"[STAGE ADVANCE SKIP] "
                f"no coordinator pane for workflow={workflow_id}"
            )
            return

        stage = item["stage"]
        next_stage = item["next_stage"]
        target_node_id = item.get("node_id") or next_stage

        project_ctx = project_for_workflow(
            workflow_id
        ) or {}

        project_name = project_ctx.get(
            "project_name",
            "legacy/unknown"
        )

        project_root = project_ctx.get(
            "project_root",
            ""
        )

        base_branch = project_ctx.get(
            "base_branch",
            ""
        )

        node = item.get("node")
        if not node:
            wf_cfg = workflow_config_for(workflow_id)
            if wf_cfg:
                node = find_node(wf_cfg, next_stage)

        policy = get_stage_policy(
            next_stage
        )

        if node:
            purpose = node.get("purpose") or policy.get("purpose", "未定义")
            integration_mode = node.get("default_integration_mode") or policy.get("default_integration_mode", "none")
            task_type = node.get("default_task_type") or policy.get("default_task_type", "feat")
            node_label = node.get("label", next_stage)
            node_type = node.get("node_type", "agent")
            agent_policy = node.get("agent_policy", {})
            req_outs = node.get("required_outputs") or policy.get("required_outputs", [])
            rules_list = node.get("rules") or policy.get("rules", [])
        else:
            purpose = policy.get("purpose", "未定义")
            integration_mode = policy.get("default_integration_mode", "none")
            task_type = policy.get("default_task_type", "test")
            node_label = item.get("stage_label", next_stage)
            node_type = "agent"
            agent_policy = {}
            req_outs = policy.get("required_outputs", [])
            rules_list = policy.get("rules", [])

        required_outputs = "\n".join(
            f"- {out}" for out in req_outs
        ) or "- 未定义"

        rules = "\n".join(
            f"- {r}" for r in rules_list
        ) or "- 未定义"

        agent_policy_text = ""
        if agent_policy:
            pref = ", ".join(agent_policy.get("preferred", [])) or "无"
            exc = ", ".join(agent_policy.get("exclude", [])) or "无"
            fix = agent_policy.get("fixed") or "无"
            agent_policy_text = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Node Agent 策略
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- 优先 Agent: {pref}
- 排除 Agent: {exc}
- 固定 Agent: {fix}
""".strip()

        try:
            while True:
                startup_record = project_for_workflow(workflow_id) or {}
                if startup_record.get("startup_ready") is False:
                    print(
                        f"[STARTUP WAIT] workflow={workflow_id} "
                        "queued event held until request is ready"
                    )
                    time.sleep(1)
                    continue

                status = coordinator_status(workflow_id)

                if status in ("idle", "done"):
                    message = f"""
HERDR_STAGE_ADVANCE_EVENT

workflow_id: {workflow_id}
project_name: {project_name}
project_root: {project_root}
base_branch: {base_branch}
completed_node: {stage}
next_node: {next_stage} ({node_label})
node_type: {node_type}

用户需求：
{project_ctx.get('requirement', '').strip() or '（未提供；请停止并等待需求正文）'}

当前工作流前置依赖已全部完成。

现在进入下一节点：

{next_stage} ({node_label})

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
节点职责
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{purpose}

默认配置：

integration_mode:
{integration_mode}

task_type:
{task_type}

必须产出：

{required_outputs}

执行规则：

{rules}

{agent_policy_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
执行要求
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. 首先执行：

   ~/herdr/bin/herdr-task list --workflow-id {workflow_id}

   阅读当前 Workflow 已完成节点的真实成果。

2. 根据前面节点的实际成果，
   决定当前节点需要创建几个 Task。

3. 不要固定前端、后端、数据库等角色。

   Pane = Task。

   Agent 根据 Task 动态选择。

4. 每个 Task 必须明确：

   - task_id
   - goal
   - acceptance criteria
   - agent
   - task_type
   - integration_mode

5. 创建 Task 必须使用：

   ~/herdr/bin/herdr-task launch

   并指定：

   --workflow-id {workflow_id}
   --node {next_stage}
   --source {project_root}

6. 默认使用本节点 policy：

   task_type={task_type}
   integration_mode={integration_mode}

   只有当前 Task 的真实性质明确需要不同配置时，
   才允许调整。

7. 不允许手工创建：

   - Clone
   - Pane
   - Branch
   - Agent

8. 可以创建一个 Task，
   也可以创建多个并行 Task。

   数量由实际工作决定。

9. 当前节点所有必要 Task 派发完成后，
   结束当前回合。

10. 后续执行、验收、返工、节点推进，
继续交给 Controller。

不要等待用户提醒。
""".strip()

                    result = subprocess.run(
                        [
                            "herdr",
                            "agent",
                            "prompt",
                            coord_pane,
                            message,
                            "--wait",
                            "--timeout",
                            "600000"
                        ],
                        text=True,
                        capture_output=True
                    )

                    if result.returncode == 0:
                        mark_stage_advance_notified(
                            workflow_id,
                            target_node_id
                        )

                        print(
                            f"[STAGE ADVANCED] "
                            f"workflow={workflow_id} "
                            f"{stage} -> {next_stage}"
                        )
                    else:
                        clear_stage_advance(
                            workflow_id,
                            target_node_id
                        )

                        print(
                            f"[STAGE ADVANCE ERROR] "
                            f"workflow={workflow_id}: "
                            f"{result.stderr.strip() or result.stdout.strip()}"
                        )

                    break

                print(
                    f"[STAGE ADVANCE WAIT] "
                    f"coordinator={status} "
                    f"workflow={workflow_id}"
                )

                time.sleep(1)

        finally:
            pass  # task_done is called by coordinator_worker dispatcher

        return

# ==============================================
# Fix Loop Event
# ==============================================

def _handle_fix_loop_item(item):
    """门禁 blocked 的回流通知:作废已由 handle_fix_loop 原子完成,
    这里只负责把 blocker 清单与修复派发指引送到总指挥。"""
    workflow_id = item["workflow_id"]
    coord_pane = coordinator_pane_for_workflow(workflow_id)

    if not coord_pane:
        print(
            f"[FIX LOOP SKIP] "
            f"no coordinator pane for workflow={workflow_id}"
        )
        return

    gate_stage = item["gate_stage"]
    retry_node = item["retry_node"]
    loop_count = item["loop_count"]
    max_loops = item["max_loops"]
    invalidated = item.get("invalidated") or []
    suggested_branch = item.get("suggested_branch")
    project_ctx = project_for_workflow(workflow_id) or {}

    blockers_text = "\n".join(
        f"- {b.get('task_id')}: {b.get('note') or '(未记录说明)'}"
        for b in item.get("blockers") or []
    ) or "- (未记录 blocker 说明,请读取 gate 阶段任务输出)"

    escalation = ""
    if loop_count >= max_loops:
        escalation = (
            f"\n注意:已达 fix-loop 上限({loop_count}/{max_loops})。"
            "先向用户请示(继续修 / 换方案 / 放弃),"
            "未经用户确认不得派发。\n"
        )

    branch_hint = (
        suggested_branch
        or "(未找到,请自行确认 retry_node 最近 committed 任务的分支)"
    )

    message = f"""
HERDR_CONTROLLER_FIX_LOOP_EVENT

workflow_id: {workflow_id}
project_name: {project_ctx.get('project_name', 'unknown')}
gate_stage: {gate_stage} — 验收结论 blocked
retry_node: {retry_node}
suggested_branch: {branch_hint}
loop_count: {loop_count}/{max_loops}
{escalation}
Controller 已自动作废受影响的 gate 与下游 Task(共 {len(invalidated)} 个,见 Task Registry);
fix 完成后 DAG 将自动按 test → review → wrapup 顺序重新推进,旧 verdict 一并作废。

Blocker 清单(blocked 结论与修复指引):
{blockers_text}

你现在只需派发修复 Task(禁止新建 workflow、禁止放弃本 workflow):

~/herdr/bin/herdr-task launch --workflow-id {workflow_id} --stage {retry_node} \\
  --onto {branch_hint} --agent auto --task-type fix \\
  --goal "修复 gate {gate_stage} 的阻断项" \\
  --acceptance "<逐条对应 Blocker 清单>" \\
  --prompt "<blocker 详情、修复范围与验证方式>"

如需再次修复,对旧 fix task 使用 --supersedes。
派发完成后结束当前回合,后续推进交给 Controller。
""".strip()

    waited = 0

    while coordinator_status(workflow_id) not in ("idle", "done"):
        if waited >= 120:
            print(
                f"[FIX LOOP WAIT TIMEOUT] "
                f"workflow={workflow_id}"
            )
            return

        time.sleep(2)
        waited += 2

    result = subprocess.run(
        [
            "herdr",
            "agent",
            "prompt",
            coord_pane,
            message,
            "--wait",
            "--timeout",
            "600000"
        ],
        text=True,
        capture_output=True
    )

    if result.returncode == 0:
        print(
            f"[FIX LOOP NOTIFIED] "
            f"workflow={workflow_id} "
            f"gate={gate_stage} "
            f"retry={retry_node}"
        )
    else:
        print(
            f"[FIX LOOP ERROR] "
            f"workflow={workflow_id}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )

# ==============================================
# Normal Task Event
# ==============================================

    task_id = item["task_id"]
    event_type = item["event_type"]
    key = item["key"]

    expected_status = (
        "blocked"
        if event_type == "blocked"
        else "agent_done"
    )

    try:
        while True:
            task = get_task(task_id)

            if not task:
                print(
                    f"[QUEUE DROP] "
                    f"task={task_id} missing"
                )
                break

            current_task_status = task.get("status")

            # 事件在等待期间已经失效
            if current_task_status != expected_status:
                print(
                    f"[QUEUE STALE] "
                    f"task={task_id} "
                    f"expected={expected_status} "
                    f"actual={current_task_status}"
                )
                break

            status = coordinator_status(task.get("workflow_id"))

            if status in ("idle", "done"):
                message = build_coordinator_message(
                    task,
                    event_type
                )

                print(
                    f"[COORDINATOR READY] "
                    f"task={task_id} "
                    f"event={event_type}"
                )

                result = subprocess.run(
                    [
                        "herdr",
                        "agent",
                        "prompt",
                        coordinator_pane_for_workflow(task.get("workflow_id")),
                        message,
                        "--wait",
                        "--timeout",
                        "600000"
                    ],
                    text=True,
                    capture_output=True
                )

                if result.returncode == 0:
                    print(
                        f"[COORDINATOR NOTIFIED] "
                        f"task={task_id} "
                        f"event={event_type}"
                    )

                    # done 事件经过总指挥正式验收后，
                    # 根据 integration_mode 自动集成并清理。
                    if event_type == "done":
                        decision = wait_for_coordinator_decision(
                            task_id
                        )

                        # 第一次没有形成决策时，只自动重试一次。
                        if decision == "agent_done":
                            decision = retry_coordinator_decision(
                                task_id
                            )

                        if decision == "completed":
                            finalize_completed_task(
                                task_id
                            )

                        elif decision == "rework":
                            print(
                                f"[FINALIZE DEFER] "
                                f"task={task_id} "
                                f"status=rework"
                            )

                        elif decision == "failed":
                            print(
                                f"[FINALIZE STOP] "
                                f"task={task_id} "
                                f"status=failed"
                            )

                        else:
                            print(
                                f"[FINALIZE WAIT] "
                                f"task={task_id} "
                                f"status={decision}"
                            )
                else:
                    print(
                        "[COORDINATOR ERROR]",
                        result.stderr.strip()
                        or result.stdout.strip()
                    )

                break

            print(
                f"[COORDINATOR BUSY] "
                f"status={status} "
                f"task={task_id}"
            )

            time.sleep(1)

    finally:
        with lock:
            queued_events.discard(key)



# ============================================================
# Agent events
# ============================================================

def handle_event(task_id, agent_status):
    task = get_task(task_id)

    if not task:
        return

    current_status = task.get("status")

    print(
        f"[TASK] "
        f"id={task_id} "
        f"workflow={task.get('workflow_id')} "
        f"stage={task['stage']} "
        f"pane={task['pane_id']} "
        f"agent={task['agent']} "
        f"status={agent_status} "
        f"task_status={current_status}"
    )

    # 终态绝不能被 Agent 普通事件覆盖
    if current_status in (
        "completed",
        "failed"
    ):
        return

    if agent_status == "working":
        if current_status in (
            "dispatched",
            "blocked",
            "rework"
        ):
            set_task_status(
                task_id,
                "working"
            )

    elif agent_status == "idle":
        # Agent 已经实际进入 working 后再回到 idle，
        # 等价于本轮交互结束。
        # dispatched -> idle 不算完成，避免尚未执行就误判。
        if current_status == "working":
            if set_task_status(
                task_id,
                "agent_done"
            ):
                task = get_task(task_id)

                enqueue_coordinator_event(
                    task,
                    "done"
                )

    elif agent_status == "blocked":
        if current_status in (
            "working",
            "dispatched",
            "rework"
        ):
            if set_task_status(
                task_id,
                "blocked"
            ):
                task = get_task(task_id)

                enqueue_coordinator_event(
                    task,
                    "blocked"
                )

    elif agent_status == "done":
        if current_status == "working":
            if set_task_status(
                task_id,
                "agent_done"
            ):
                task = get_task(task_id)

                enqueue_coordinator_event(
                    task,
                    "done"
                )


# ============================================================
# Crash recovery / startup reconciliation
# ============================================================

def get_agent_runtime_status(pane_id):
    try:
        output = subprocess.check_output(
            [
                "herdr",
                "agent",
                "get",
                pane_id
            ],
            text=True
        )

        data = json.loads(output)

        return (
            data["result"]["agent"]
            .get("agent_status", "unknown")
        )

    except Exception as e:
        print(
            f"[RECOVERY STATUS ERROR] "
            f"pane={pane_id}: {e}"
        )
        return None


def reconcile_task_state(task_id):
    task = get_task(task_id)

    if not task:
        return

    current = task.get("status")

    if current in (
        "completed",
        "committed",
        "integrated",
        "cleanup_ready",
        "cleaned",
        "failed"
    ):
        return

    # Registry 已经知道 Agent 执行结束，
    # 但 Controller 可能在通知总指挥前重启。
    if current == "agent_done":
        print(
            f"[RECOVERY] "
            f"task={task_id} "
            f"registry=agent_done "
            f"→ restore done event"
        )

        enqueue_coordinator_event(
            task,
            "done"
        )
        return

    pane_id = task.get("pane_id")

    if not pane_id:
        return

    runtime = get_agent_runtime_status(
        pane_id
    )

    if runtime is None:
        return

    print(
        f"[RECOVERY] "
        f"task={task_id} "
        f"registry={current} "
        f"agent={runtime}"
    )

    # --------------------------------
    # Agent 当前正在运行
    # --------------------------------
    if runtime == "working":
        if current in (
            "dispatched",
            "blocked",
            "rework"
        ):
            set_task_status(
                task_id,
                "working"
            )
        return

    # --------------------------------
    # Agent 当前 blocked
    # --------------------------------
    if runtime == "blocked":
        if current in (
            "dispatched",
            "working",
            "rework"
        ):
            if not set_task_status(
                task_id,
                "blocked"
            ):
                return

        task = get_task(task_id)

        if task and task.get("status") == "blocked":
            enqueue_coordinator_event(
                task,
                "blocked"
            )

        return

    # --------------------------------
    # Agent 已经 done，但 Controller
    # 错过了 working/done 事件
    # --------------------------------
    if runtime == "done":
        current = get_task(task_id).get(
            "status"
        )

        if current == "dispatched":
            if not set_task_status(
                task_id,
                "working"
            ):
                return

            current = "working"

        elif current in (
            "blocked",
            "rework"
        ):
            if not set_task_status(
                task_id,
                "working"
            ):
                return

            current = "working"

        if current == "working":
            if not set_task_status(
                task_id,
                "agent_done"
            ):
                return

        task = get_task(task_id)

        if task and task.get("status") == "agent_done":
            enqueue_coordinator_event(
                task,
                "done"
            )

        return

    # Agent 曾经进入 working，随后 Controller 重启时发现已经 idle，
    # 视为本轮执行已经结束。
    if runtime == "idle" and current == "working":
        if not set_task_status(
            task_id,
            "agent_done"
        ):
            return

        task = get_task(task_id)

        if task and task.get("status") == "agent_done":
            enqueue_coordinator_event(
                task,
                "done"
            )

        return

    # dispatched -> idle 不自动推断完成；
    # unknown 也不自动推断。
    print(
        f"[RECOVERY NOOP] "
        f"task={task_id} "
        f"agent={runtime}"
    )



# ============================================================
# Per-task Herdr subscriptions
# ============================================================

def listen_task(task_id):
    task = get_task(task_id)

    if not task:
        return

    pane_id = task["pane_id"]

    sock = socket.socket(
        socket.AF_UNIX,
        socket.SOCK_STREAM
    )

    try:
        sock.connect(SOCKET_PATH)

        with lock:
            task_sockets[task_id] = sock

        request = {
            "id": f"task-{task_id}",
            "method": "events.subscribe",
            "params": {
                "subscriptions": [
                    {
                        "type":
                        "pane.agent_status_changed",
                        "pane_id": pane_id
                    }
                ]
            }
        }

        sock.sendall(
            (json.dumps(request) + "\n").encode()
        )

        file = sock.makefile("r")

        first = file.readline()

        if first:
            print(
                f"[SUBSCRIBED] "
                f"task={task_id} "
                f"pane={pane_id}"
            )

            # Controller 重启后立即核对
            # Registry 与 Agent 当前真实状态。
            reconcile_task_state(
                task_id
            )

        for line in file:
            if not line:
                break

            event = json.loads(line)

            if (
                event.get("event")
                != "pane.agent_status_changed"
            ):
                continue

            status = (
                event["data"]
                .get(
                    "agent_status",
                    "unknown"
                )
            )

            handle_event(
                task_id,
                status
            )

    except Exception as e:
        print(
            f"[LISTENER ERROR] "
            f"task={task_id}: {e}"
        )

    finally:
        with lock:
            task_sockets.pop(
                task_id,
                None
            )
            listeners.pop(
                task_id,
                None
            )

        try:
            sock.close()
        except Exception:
            pass


def start_task_listener(task_id):
    thread = threading.Thread(
        target=listen_task,
        args=(task_id,),
        daemon=True
    )

    with lock:
        listeners[task_id] = thread

    thread.start()


def stop_task_listener(task_id):
    with lock:
        sock = task_sockets.pop(
            task_id,
            None
        )

    if sock:
        try:
            sock.shutdown(
                socket.SHUT_RDWR
            )
        except Exception:
            pass

        try:
            sock.close()
        except Exception:
            pass

        print(
            f"[UNSUBSCRIBED] "
            f"task={task_id}"
        )


# ============================================================
# Registry watcher
# ============================================================

def registry_watcher():
    active_statuses = {
        "dispatched",
        "working",
        "blocked",
        "agent_done",
        "rework",
    }

    last_advance_check = 0

    while True:
        try:
            now = time.time()
            if now - last_advance_check >= 2:
                last_advance_check = now
                check_all_workflows_stage_advance()

            tasks = load_tasks()

            for task in tasks:
                task_id = task["task_id"]
                status = task.get("status")

                if status in (
                    "completed",
                    "failed"
                ):
                    with lock:
                        running = (
                            task_id
                            in task_sockets
                        )

                    if running:
                        stop_task_listener(
                            task_id
                        )

                    continue

                if status in active_statuses:
                    with lock:
                        already = (
                            task_id
                            in listeners
                        )

                    if not already:
                        start_task_listener(
                            task_id
                        )

        except Exception as e:
            print(
                f"[REGISTRY ERROR] {e}"
            )

        time.sleep(1)


# ============================================================
# Main
# ============================================================

def main():
    print("[CONTROLLER V12] starting")
    print(f"[REGISTRY] {TASKS_FILE}")
    print(
        f"[COORDINATOR] "
        f"{COORDINATOR_PANE}"
    )
    print(
        "[QUEUE] coordinator event "
        "serialization enabled"
    )

    reset_queued_stage_states()

    worker = threading.Thread(
        target=coordinator_worker,
        daemon=True
    )
    worker.start()

    registry_watcher()


if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print(
            "\n[CONTROLLER V12] stopped"
        )
