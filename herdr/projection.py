"""Telemetry Projection Engine (herdr/projection.py).

Cleanses raw terminal streams, extracts structured 4D telemetries
(Intent, Milestones, Artifacts First-Class Citizens, Blockers),
and produces high signal-to-noise white-box briefings for human operators.
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ANSI_REGEX = re.compile(
    r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\].*?(?:\x07|\x1b\\))"
)


def strip_ansi_codes(text: str) -> str:
    """Remove ANSI escape sequences, control codes, and normalize linebreaks."""
    if not text:
        return ""
    clean = ANSI_REGEX.sub("", text)
    clean = clean.replace("\r\n", "\n").replace("\r", "\n")
    # Clean terminal bell and non-printable control characters (except newline, tab)
    clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", clean)
    return clean


def _read_pane_content(pane_id: str) -> str:
    """Read visible content from Herdr pane."""
    if not pane_id:
        return ""
    try:
        r = subprocess.run(
            ["herdr", "pane", "read", pane_id, "--source", "visible"],
            text=True,
            capture_output=True,
            timeout=5,
        )
        return (r.stdout or "") + "\n" + (r.stderr or "")
    except Exception:
        return ""


def get_tasks_file() -> Path:
    p = os.environ.get("TASKS_FILE")
    if p:
        return Path(p)
    return Path.home() / ".herdr-controller" / "tasks.json"


def get_workflows_file() -> Path:
    p = os.environ.get("WORKFLOWS_FILE")
    if p:
        return Path(p)
    return Path.home() / ".herdr-controller" / "workflows.json"


def load_tasks_data() -> Dict[str, Any]:
    f = get_tasks_file()
    if not f.exists():
        return {"tasks": []}
    try:
        with open(f, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return {"tasks": []}


def load_workflows_data() -> Dict[str, Any]:
    f = get_workflows_file()
    if not f.exists():
        return {"workflows": {}}
    try:
        with open(f, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return {"workflows": {}}


def extract_task_intent(task: Dict[str, Any], terminal_text: str) -> str:
    """Extract current intent from terminal text or fallback to task goal."""
    clean = strip_ansi_codes(terminal_text)
    
    # 1. Check for explicit [HERDR_INTENT] or [INTENT] markers
    intent_match = re.search(r"\[(?:HERDR_)?INTENT\][:\s]*(.+)", clean, re.IGNORECASE)
    if intent_match:
        return intent_match.group(1).strip()

    # 2. Fallback to task goal as primary intent
    goal = task.get("goal")
    if goal:
        return str(goal).strip()

    # 3. Check for recent running actions in terminal
    action_match = re.search(r"(?:Running|Executing|Testing|Building|Compiling)\s+([^\n\r]+)", clean)
    if action_match:
        return f"正在执行: {action_match.group(0).strip()}"

    return f"{task.get('node_label') or task.get('node') or '工位执行'} 进行中"


def extract_task_milestones(task: Dict[str, Any], terminal_text: str) -> List[Dict[str, Any]]:
    """Extract ordered milestones and their statuses."""
    status = task.get("status")
    clone_path = Path(task.get("clone_path") or "")
    loop_dir = clone_path / ".herdr-loop"

    milestones = [
        {"label": "锁定验收目标与环境契约", "status": "completed" if clone_path.exists() else "pending"},
        {"label": "核心代码实现与迭代开发", "status": "pending"},
        {"label": "内循环自检与质量门禁", "status": "pending"},
        {"label": "交付产物会签与状态收尾", "status": "pending"},
    ]

    if status in {"completed", "committed", "integrated", "cleanup_ready", "cleaned"}:
        for m in milestones:
            m["status"] = "completed"
        return milestones

    if status in {"working", "dispatched", "rework", "blocked", "paused", "interrupted"}:
        milestones[0]["status"] = "completed"
        milestones[1]["status"] = "in_progress"

        if (loop_dir / "STATE.md").exists() or (loop_dir / "EVALUATION.md").exists():
            milestones[1]["status"] = "completed"
            milestones[2]["status"] = "in_progress"

        if status == "agent_done":
            milestones[2]["status"] = "completed"
            milestones[3]["status"] = "in_progress"

    return milestones


def collect_task_artifacts(task: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect artifacts produced by the task as first-class citizens."""
    artifacts: List[Dict[str, Any]] = []
    clone_path_str = task.get("clone_path")
    if not clone_path_str:
        return artifacts

    clone_path = Path(clone_path_str)
    if not clone_path.exists():
        return artifacts

    # 1. Git Changes / Diff Artifact
    try:
        r_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(clone_path),
            text=True,
            capture_output=True,
            timeout=5,
        )
        status_lines = [l for l in (r_status.stdout or "").splitlines() if l.strip()]
        if status_lines:
            files_list = [l[3:].strip() for l in status_lines]
            r_diff = subprocess.run(
                ["git", "diff", "--stat"],
                cwd=str(clone_path),
                text=True,
                capture_output=True,
                timeout=5,
            )
            diff_summary = r_diff.stdout.strip().splitlines()[-1] if r_diff.stdout.strip() else f"{len(files_list)} 个文件变更"
            artifacts.append({
                "kind": "diff",
                "name": "Git 工作区变更",
                "summary": diff_summary,
                "files_changed": len(files_list),
                "files": files_list[:15],
            })
    except Exception:
        pass

    # 2. Evaluation Report Artifact
    eval_file = clone_path / ".herdr-loop" / "EVALUATION.md"
    if eval_file.exists():
        try:
            content = eval_file.read_text(encoding="utf-8")
            score_match = re.search(r"Overall Score[\*:\s]*([\d.]+)", content, re.IGNORECASE)
            score = float(score_match.group(1)) if score_match else None
            passed = "PASSED" in content or (score is not None and score >= 100.0)
            artifacts.append({
                "kind": "evaluation",
                "name": "工位自检评分报告",
                "score": score,
                "passed": passed,
                "path": str(eval_file),
                "summary": f"自检评分: {score or 0.0} / 100.0 ({'通过' if passed else '未达标'})",
            })
        except Exception:
            pass

    # 3. Document Artifacts (Goal & deliverables)
    goal_file = clone_path / ".herdr-loop" / "GOAL.md"
    if goal_file.exists():
        artifacts.append({
            "kind": "document",
            "name": "工位目标与验收标准契约",
            "path": str(goal_file),
            "summary": "工位量化验收契约文档 (.herdr-loop/GOAL.md)",
        })

    # Search for other markdown deliverables in docs/ or root
    for doc_candidate in clone_path.glob("docs/**/*.md"):
        if ".herdr-loop" not in str(doc_candidate):
            rel = doc_candidate.relative_to(clone_path)
            artifacts.append({
                "kind": "document",
                "name": doc_candidate.name,
                "path": str(doc_candidate),
                "summary": f"设计与交付文档 ({rel})",
            })

    return artifacts


def extract_recent_activity(clean_text: str, max_lines: int = 5) -> str:
    """Extract clean recent activity log lines from terminal buffer."""
    if not clean_text:
        return ""
    lines = [l.strip() for l in clean_text.splitlines() if l.strip()]
    if not lines:
        return ""
    return "\n".join(lines[-max_lines:])


def project_task(task_id: str) -> Dict[str, Any]:
    """Produce the complete 4D white-box telemetry projection for a task."""
    tasks_data = load_tasks_data()
    task = next((t for t in tasks_data.get("tasks", []) if t.get("task_id") == task_id), None)
    if not task:
        raise ValueError(f"Task '{task_id}' not found")

    pane_id = task.get("pane_id")
    raw_terminal = _read_pane_content(pane_id) if pane_id else ""
    clean_terminal = strip_ansi_codes(raw_terminal)

    intent = extract_task_intent(task, clean_terminal)
    milestones = extract_task_milestones(task, clean_terminal)
    artifacts = collect_task_artifacts(task)

    # Detect blockers
    blocker = None
    if task.get("status") == "blocked":
        blocker = task.get("sentinel_reason") or "工位遇到阻碍，已上报仲裁"
    elif task.get("status") == "interrupted":
        blocker = f"已制动暂停: {task.get('interrupt_reason') or '人工干预'}"
    elif "HERDR_TASK_BLOCKER" in clean_terminal:
        blocker = "终端输出阻碍标记 HERDR_TASK_BLOCKER"

    recent_activity = extract_recent_activity(clean_terminal, max_lines=6)

    return {
        "task_id": task_id,
        "workflow_id": task.get("workflow_id"),
        "node": task.get("node") or task.get("stage"),
        "node_label": task.get("node_label") or task.get("stage_label") or task.get("node"),
        "agent": task.get("agent"),
        "status": task.get("status"),
        "goal": task.get("goal"),
        "intent": intent,
        "milestones": milestones,
        "artifacts": artifacts,
        "blocker": blocker,
        "recent_activity": recent_activity,
        "projected_at": time.time(),
    }


def project_workflow(workflow_id: str) -> Dict[str, Any]:
    """Produce aggregated white-box projection across all tasks in a workflow."""
    wf_data = load_workflows_data()
    wf_entry = wf_data.get("workflows", {}).get(workflow_id)
    if not wf_entry:
        raise ValueError(f"Workflow '{workflow_id}' not found")

    tasks_data = load_tasks_data()
    wf_tasks = [t for t in tasks_data.get("tasks", []) if t.get("workflow_id") == workflow_id]

    task_projections = []
    completed_count = 0
    blocked_count = 0

    for t in wf_tasks:
        try:
            p = project_task(t["task_id"])
            task_projections.append(p)
            if p["status"] in {"completed", "committed", "integrated", "cleanup_ready", "cleaned"}:
                completed_count += 1
            elif p["status"] == "blocked":
                blocked_count += 1
        except Exception:
            pass

    return {
        "workflow_id": workflow_id,
        "status": wf_entry.get("status"),
        "progress": {
            "total_tasks": len(wf_tasks),
            "completed_tasks": completed_count,
            "blocked_tasks": blocked_count,
        },
        "tasks": task_projections,
        "projected_at": time.time(),
    }
