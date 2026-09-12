#!/opt/homebrew/bin/python3
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

HOME = Path.home()
ROOT = HOME / ".herdr-controller"
PROJECTS_FILE = ROOT / "projects.json"
WORKFLOWS_FILE = ROOT / "workflows.json"
LEGACY_WORKFLOW_FILE = ROOT / "workflow.json"

STAGES = [
    ("requirements", "2需求分析", "plan"),
    ("plan", "3计划", "implementation"),
    ("implementation", "4实现", "test"),
    ("test", "5测试", "review"),
    ("review", "6评审", "wrapup"),
    ("wrapup", "7收尾", None),
]


def _load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _run(cmd, check=True):
    return subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        check=check,
    )


def _run_json(cmd):
    result = _run(cmd, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip()
            or result.stdout.strip()
            or f"command failed: {' '.join(cmd)}"
        )
    try:
        return json.loads(result.stdout)
    except Exception as exc:
        raise RuntimeError(
            f"invalid JSON from {' '.join(cmd)}: {result.stdout[:500]}"
        ) from exc


def canonical_root(path):
    return str(Path(path).expanduser().resolve())


def detect_git_root(cwd=None):
    cwd = canonical_root(cwd or os.getcwd())
    result = _run(
        ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "当前目录不在 Git 项目中。请先 cd 到目标项目，再运行 herdr-factory。"
        )
    return canonical_root(result.stdout.strip())


def project_id_for(root):
    root = canonical_root(root)
    base = Path(root).name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", base).strip("-") or "project"
    digest = hashlib.sha1(root.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def detect_base_branch(root):
    root = canonical_root(root)

    current = _run(
        ["git", "-C", root, "branch", "--show-current"],
        check=False,
    )
    branch = current.stdout.strip()
    if current.returncode == 0 and branch:
        return branch

    remote_head = _run(
        ["git", "-C", root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        check=False,
    )
    value = remote_head.stdout.strip()
    if remote_head.returncode == 0 and value.startswith("origin/"):
        return value.split("/", 1)[1]

    for candidate in ("dev", "main", "master"):
        probe = _run(
            ["git", "-C", root, "show-ref", "--verify", "--quiet", f"refs/heads/{candidate}"],
            check=False,
        )
        if probe.returncode == 0:
            return candidate

    raise RuntimeError(
        f"无法确定项目基础分支: {root}。请先切到项目的正常开发分支后重试。"
    )


def load_projects():
    return _load(PROJECTS_FILE, {"version": 1, "projects": {}})


def save_projects(data):
    _save(PROJECTS_FILE, data)


def load_workflows():
    return _load(WORKFLOWS_FILE, {"version": 1, "workflows": {}})


def save_workflows(data):
    _save(WORKFLOWS_FILE, data)


def project_by_root(root):
    root = canonical_root(root)
    return load_projects().get("projects", {}).get(root)


def project_for_workflow(workflow_id):
    record = (
        load_workflows()
        .get("workflows", {})
        .get(workflow_id)
    )
    return record


def workflow_config_for(workflow_id):
    record = project_for_workflow(workflow_id)
    if record and record.get("workflow_file"):
        path = Path(record["workflow_file"]).expanduser()
        if path.exists():
            return _load(path, None)
    if LEGACY_WORKFLOW_FILE.exists():
        return _load(LEGACY_WORKFLOW_FILE, None)
    return None


def register_workflow(workflow_id, project):
    data = load_workflows()
    data.setdefault("workflows", {})[workflow_id] = {
        "workflow_id": workflow_id,
        "project_id": project["project_id"],
        "project_name": project["project_name"],
        "project_root": project["project_root"],
        "base_branch": project.get("base_branch"),
        "workspace_id": project["workspace_id"],
        "coordinator_pane_id": project["coordinator_pane_id"],
        "workflow_file": project["workflow_file"],
    }
    save_workflows(data)


def _workspace_alive(workspace_id):
    result = _run(
        ["herdr", "workspace", "get", workspace_id],
        check=False,
    )
    return result.returncode == 0


def _pane_alive(pane_id):
    result = _run(
        ["herdr", "pane", "get", pane_id],
        check=False,
    )
    return result.returncode == 0


def _coordinator_alive(pane_id):
    result = _run(
        ["herdr", "agent", "get", pane_id],
        check=False,
    )
    return result.returncode == 0


def _start_coordinator(project_id, coordinator_pane_id):
    # Herdr agent name must:
    # - start with lowercase letter
    # - contain only a-z, 0-9, - or _
    # - max 32 chars
    safe_id = re.sub(
        r"[^a-z0-9_-]+",
        "-",
        str(project_id).lower()
    ).strip("-_")

    if not safe_id or not safe_id[0].isalpha():
        safe_id = "project-" + safe_id

    agent_name = (safe_id + "-coordinator")[:32]

    return _run_json([
        "herdr",
        "agent",
        "start",
        agent_name,
        "--kind",
        "opencode",
        "--pane",
        coordinator_pane_id,
        "--timeout",
        "120000",
        "--",
        "--auto",
    ])

def project_alive(project):
    if not project:
        return False
    if not _workspace_alive(project.get("workspace_id", "")):
        return False
    if not _pane_alive(project.get("coordinator_pane_id", "")):
        return False
    workflow = _load(project.get("workflow_file", ""), None)
    if not workflow:
        return False
    for stage in workflow.get("stages", []):
        if not _pane_alive(stage.get("anchor_pane_id", "")):
            return False
    return True


def import_legacy_project(root, legacy_workflow=None):
    root = canonical_root(root)
    if project_by_root(root):
        return project_by_root(root)

    legacy_path = Path(
        legacy_workflow or LEGACY_WORKFLOW_FILE
    ).expanduser()
    if not legacy_path.exists():
        return None

    workflow = _load(legacy_path, None)
    if not workflow:
        return None

    workspace_id = workflow.get("workspace_id")
    coordinator = workflow.get("coordinator", {})
    coordinator_pane_id = coordinator.get("pane_id")

    if not workspace_id or not coordinator_pane_id:
        return None

    if not _workspace_alive(workspace_id):
        return None

    project_id = project_id_for(root)
    project_name = Path(root).name
    project_dir = ROOT / "projects" / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    workflow_file = project_dir / "workflow.json"

    workflow["project_id"] = project_id
    workflow["project_name"] = project_name
    workflow["project_root"] = root
    workflow["base_branch"] = detect_base_branch(root)
    workflow_file.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    record = {
        "project_id": project_id,
        "project_name": project_name,
        "project_root": root,
        "base_branch": detect_base_branch(root),
        "workspace_id": workspace_id,
        "coordinator_pane_id": coordinator_pane_id,
        "workflow_file": str(workflow_file),
    }

    data = load_projects()
    data.setdefault("projects", {})[root] = record
    save_projects(data)
    return record


def provision_project(root):
    root = canonical_root(root)
    project_id = project_id_for(root)
    project_name = Path(root).name

    created = _run_json([
        "herdr",
        "workspace",
        "create",
        "--cwd",
        root,
        "--label",
        project_name,
        "--no-focus",
    ])

    result = created["result"]
    workspace_id = result["workspace"]["workspace_id"]
    coordinator_tab_id = result["tab"]["tab_id"]
    coordinator_pane_id = result["root_pane"]["pane_id"]

    _run([
        "herdr",
        "tab",
        "rename",
        coordinator_tab_id,
        "1总指挥",
    ])
    _run([
        "herdr",
        "pane",
        "rename",
        coordinator_pane_id,
        "总指挥",
    ])

    _start_coordinator(project_id, coordinator_pane_id)

    stages = []
    for key, label, next_stage in STAGES:
        created_tab = _run_json([
            "herdr",
            "tab",
            "create",
            "--workspace",
            workspace_id,
            "--cwd",
            root,
            "--label",
            label,
            "--no-focus",
        ])
        tab_result = created_tab["result"]
        tab_id = tab_result["tab"]["tab_id"]
        anchor_pane_id = tab_result["root_pane"]["pane_id"]
        stages.append({
            "key": key,
            "label": label,
            "tab_id": tab_id,
            "order": len(stages) + 1,
            "next": next_stage,
            "anchor_pane_id": anchor_pane_id,
        })

    workflow = {
        "project_id": project_id,
        "project_name": project_name,
        "project_root": root,
        "base_branch": detect_base_branch(root),
        "workspace_id": workspace_id,
        "coordinator": {
            "tab_id": coordinator_tab_id,
            "label": "1总指挥",
            "pane_id": coordinator_pane_id,
        },
        "stages": stages,
    }

    project_dir = ROOT / "projects" / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    workflow_file = project_dir / "workflow.json"
    workflow_file.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    record = {
        "project_id": project_id,
        "project_name": project_name,
        "project_root": root,
        "base_branch": detect_base_branch(root),
        "workspace_id": workspace_id,
        "coordinator_pane_id": coordinator_pane_id,
        "workflow_file": str(workflow_file),
    }

    data = load_projects()
    data.setdefault("projects", {})[root] = record
    save_projects(data)
    return record


def ensure_project(root):
    root = canonical_root(root)
    record = project_by_root(root)

    if record:
        workspace_id = record.get("workspace_id", "")
        coordinator_pane_id = record.get("coordinator_pane_id", "")

        # Existing registered project: keep the existing Workspace.
        # Do NOT reprovision the entire project merely because an anchor Pane
        # or Agent lifecycle check looks stale. User-created/retained Panes
        # are part of the project state and must be preserved.
        if _workspace_alive(workspace_id):
            if not _pane_alive(coordinator_pane_id):
                raise RuntimeError(
                    "Registered Herdr Workspace is alive but coordinator Pane "
                    f"is missing: workspace={workspace_id} "
                    f"pane={coordinator_pane_id}. Refusing automatic reprovision."
                )

            if not _coordinator_alive(coordinator_pane_id):
                _start_coordinator(
                    record["project_id"],
                    coordinator_pane_id,
                )

            return record

        # Only a genuinely missing Workspace is provisioned again.
        return provision_project(root)

    return provision_project(root)
