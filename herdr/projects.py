#!/opt/homebrew/bin/python3
import datetime
import fcntl
import hashlib
import json
import os
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path

HOME = Path.home()
ROOT = HOME / ".herdr-controller"
PROJECTS_FILE = ROOT / "projects.json"
WORKFLOWS_FILE = ROOT / "workflows.json"
LEGACY_WORKFLOW_FILE = ROOT / "workflow.json"

try:
    from .workflow import load_template, normalize_workflow, find_node
except ImportError:
    from herdr.workflow import load_template, normalize_workflow, find_node

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


TERMINAL_WORKFLOW_STATUSES = {"completed"}


def active_workflows_for_project(project_id):
    """Registry entries of project_id that have not reached a terminal status."""
    return [
        entry
        for entry in load_workflows().get("workflows", {}).values()
        if entry.get("project_id") == project_id
        and entry.get("status") not in TERMINAL_WORKFLOW_STATUSES
    ]


def non_terminal_workflow_ids():
    return {
        workflow_id
        for workflow_id, entry in load_workflows().get("workflows", {}).items()
        if entry.get("status") not in TERMINAL_WORKFLOW_STATUSES
    }


def workflow_closed(workflow_id):
    entry = project_for_workflow(workflow_id)
    return bool(entry) and entry.get("status") in TERMINAL_WORKFLOW_STATUSES


def workflow_registered(workflow_id):
    return bool(project_for_workflow(workflow_id))


@contextmanager
def workflow_creation_lock(project_id):
    """Serialize same-project workflow creation.

    The active-workflow check and register_workflow must be atomic, or two
    concurrent creates can both observe "no active workflow" and register.
    """
    lock_dir = ROOT / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_dir / f"{project_id}.workflow-create.lock", os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


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
            cfg = _load(path, None)
            if cfg:
                return normalize_workflow(cfg)
    if LEGACY_WORKFLOW_FILE.exists():
        cfg = _load(LEGACY_WORKFLOW_FILE, None)
        if cfg:
            return normalize_workflow(cfg)
    return None


def save_workflow_config_for(workflow_id, workflow_data):
    record = project_for_workflow(workflow_id)
    if record and record.get("workflow_file"):
        path = Path(record["workflow_file"]).expanduser()
        _save(path, workflow_data)
        return True
    if LEGACY_WORKFLOW_FILE.exists():
        _save(LEGACY_WORKFLOW_FILE, workflow_data)
        return True
    return False


SUBJECT_MAX_LEN = 64
_SUBJECT_MARKDOWN_PREFIX = re.compile(
    r"^(#{1,6}\s+|[-*+>]+\s+|\d+[.、)]\s*|\[[ xX]\]\s*)+"
)


def requirement_subject(requirement=""):
    """Extract a one-line human-readable subject from a free-form requirement.

    Prefers the first non-heading line (a bare `## 需求` title says nothing),
    strips markdown heading/list/emphasis markers, then truncates.
    """
    candidates = []
    for raw in str(requirement or "").splitlines():
        text = raw.strip()
        stripped = _SUBJECT_MARKDOWN_PREFIX.sub("", text).strip()
        if not stripped:
            continue
        candidates.append((text.startswith("#"), stripped))
    if not candidates:
        return ""
    line = next((s for is_heading, s in candidates if not is_heading), None)
    if line is None:
        line = candidates[0][1]
    line = re.sub(r"\s{2,}", " ", line.replace("**", "").replace("`", ""))
    if len(line) > SUBJECT_MAX_LEN:
        return line[: SUBJECT_MAX_LEN - 1] + "…"
    return line


def generate_workflow_id(project, prefix="wf", now=None):
    """Generate human-readable workflow ID using Option A: wf-{project}-{MMDD}-{seq:02d}."""
    if now is None:
        now = datetime.datetime.now()
    name = project.get("project_name") or Path(project.get("project_root", "")).name or "project"
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-") or "project"
    date_str = now.strftime("%m%d")
    expected_prefix = f"{prefix}-{slug}-{date_str}-"

    # Scan existing workflows in registry to find next sequence number
    existing_seqs = []
    workflows = load_workflows().get("workflows", {})
    for wid in workflows:
        if wid.startswith(expected_prefix):
            remainder = wid[len(expected_prefix):]
            if remainder.isdigit():
                existing_seqs.append(int(remainder))
            elif "-" in remainder:
                first_part = remainder.split("-")[0]
                if first_part.isdigit():
                    existing_seqs.append(int(first_part))

    next_seq = max(existing_seqs, default=0) + 1
    candidate_id = f"{expected_prefix}{next_seq:02d}"
    while candidate_id in workflows:
        next_seq += 1
        candidate_id = f"{expected_prefix}{next_seq:02d}"

    return candidate_id


def register_workflow(workflow_id, project, requirement="", title=""):
    title = (title or "").strip()
    subject = title or requirement_subject(requirement) or "未命名工作流"
    data = load_workflows()
    data.setdefault("workflows", {})[workflow_id] = {
        "workflow_id": workflow_id,
        "title": title,
        "requirement_subject": subject,
        "project_id": project["project_id"],
        "project_name": project["project_name"],
        "project_root": project["project_root"],
        "base_branch": project.get("base_branch"),
        "workspace_id": project["workspace_id"],
        "coordinator_pane_id": project["coordinator_pane_id"],
        "workflow_file": project["workflow_file"],
        "requirement": requirement,
        "startup_ready": False,
    }
    save_workflows(data)


def mark_workflow_startup_ready(workflow_id, healthy_agents=None, unhealthy_agents=None):
    data = load_workflows()
    record = data.setdefault("workflows", {}).get(workflow_id)
    if not record:
        raise RuntimeError(f"Workflow registry missing: {workflow_id}")
    record["startup_ready"] = True
    if healthy_agents is not None:
        record["healthy_agents"] = healthy_agents
    if unhealthy_agents is not None:
        record["unhealthy_agents"] = unhealthy_agents
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

    # Stage tabs/anchors are repairable topology, not project identity.
    # Missing anchors must never reprovision the whole workspace.
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


def provision_project(root, template_name="software-development-v1"):
    root = canonical_root(root)
    project_id = project_id_for(root)
    project_name = Path(root).name

    template = load_template(template_name)
    nodes = template.get("nodes", [])

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

    runtime_nodes = []
    for node in nodes:
        created_tab = _run_json([
            "herdr",
            "tab",
            "create",
            "--workspace",
            workspace_id,
            "--cwd",
            root,
            "--label",
            node["label"],
            "--no-focus",
        ])
        tab_result = created_tab["result"]
        tab_id = tab_result["tab"]["tab_id"]
        anchor_pane_id = tab_result["root_pane"]["pane_id"]
        _run([
            "herdr",
            "pane",
            "rename",
            anchor_pane_id,
            "Anchor",
        ], check=False)

        n = dict(node)
        n["tab_id"] = tab_id
        n["anchor_pane_id"] = anchor_pane_id
        runtime_nodes.append(n)

    workflow = {
        "project_id": project_id,
        "project_name": project_name,
        "project_root": root,
        "base_branch": detect_base_branch(root),
        "workspace_id": workspace_id,
        "workflow_template": template.get("name", template_name),
        "coordinator": {
            "tab_id": coordinator_tab_id,
            "label": "1总指挥",
            "pane_id": coordinator_pane_id,
        },
        "nodes": runtime_nodes,
    }
    workflow = normalize_workflow(workflow)

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


def ensure_project(root, template_name="software-development-v1"):
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
        return provision_project(root, template_name=template_name)

    return provision_project(root, template_name=template_name)


def ensure_node_runtime(workflow_id_or_root, node_id):
    """Ensure that the Tab and Anchor Pane for a node exist and are healthy.

    If the Tab was closed/deleted, recreate it.
    If the Anchor Pane was closed/purged, detect or split a new Anchor Pane.
    Persists repaired runtime mappings to workflow.json.
    """
    workflow_id = None
    project = None
    workflow_cfg = None

    if project_for_workflow(workflow_id_or_root):
        workflow_id = workflow_id_or_root
        project = project_for_workflow(workflow_id)
        workflow_cfg = workflow_config_for(workflow_id)
    else:
        project = project_by_root(workflow_id_or_root)
        if project:
            path = Path(project.get("workflow_file", "")).expanduser()
            workflow_cfg = normalize_workflow(_load(path, None)) if path.exists() else None
        else:
            # Maybe it's a project_id or workflow_id that needs lookup in workflows
            all_wf = load_workflows().get("workflows", {})
            if workflow_id_or_root in all_wf:
                workflow_id = workflow_id_or_root
                project = all_wf[workflow_id]
                workflow_cfg = workflow_config_for(workflow_id)

    if not workflow_cfg:
        raise RuntimeError(f"Workflow config missing for: {workflow_id_or_root}")

    workspace_id = workflow_cfg.get("workspace_id")
    if not workspace_id or not _workspace_alive(workspace_id):
        raise RuntimeError(f"Herdr workspace is not alive: {workspace_id}")

    project_root = workflow_cfg.get("project_root", os.getcwd())

    # Find node (by id or stage key)
    node = find_node(workflow_cfg, node_id)
    if not node:
        raise RuntimeError(f"Node '{node_id}' not found in workflow definition.")

    tab_id = node.get("tab_id")
    anchor_pane_id = node.get("anchor_pane_id")
    label = node.get("label", node_id)
    repaired = False

    # Check Tab
    tab_alive = False
    if tab_id:
        res = _run(["herdr", "tab", "get", tab_id], check=False)
        tab_alive = (res.returncode == 0)

    if not tab_alive:
        created_tab = _run_json([
            "herdr", "tab", "create",
            "--workspace", workspace_id,
            "--cwd", project_root,
            "--label", label,
            "--no-focus",
        ])
        tab_result = created_tab["result"]
        tab_id = tab_result["tab"]["tab_id"]
        anchor_pane_id = tab_result["root_pane"]["pane_id"]
        _run(["herdr", "pane", "rename", anchor_pane_id, "Anchor"], check=False)
        node["tab_id"] = tab_id
        node["anchor_pane_id"] = anchor_pane_id
        repaired = True
    else:
        # Tab is alive, check Anchor Pane
        anchor_alive = False
        if anchor_pane_id:
            res = _run(["herdr", "pane", "get", anchor_pane_id], check=False)
            anchor_alive = (res.returncode == 0)

        if not anchor_alive:
            panes_data = _run_json(["herdr", "pane", "list", "--workspace", workspace_id])
            panes_list = panes_data.get("result", {}).get("panes", [])
            tab_panes = [p for p in panes_list if p.get("tab_id") == tab_id]

            if tab_panes:
                anchor_candidates = [p for p in tab_panes if p.get("label") == "Anchor"]
                if anchor_candidates:
                    anchor_pane_id = anchor_candidates[0]["pane_id"]
                else:
                    parent_pane = tab_panes[0]["pane_id"]
                    split_res = _run_json([
                        "herdr", "pane", "split",
                        parent_pane,
                        "--direction", "right",
                        "--cwd", project_root,
                        "--no-focus",
                    ])
                    anchor_pane_id = split_res["result"]["pane"]["pane_id"]
                    _run(["herdr", "pane", "rename", anchor_pane_id, "Anchor"], check=False)

                node["anchor_pane_id"] = anchor_pane_id
                repaired = True
            else:
                created_tab = _run_json([
                    "herdr", "tab", "create",
                    "--workspace", workspace_id,
                    "--cwd", project_root,
                    "--label", label,
                    "--no-focus",
                ])
                tab_result = created_tab["result"]
                tab_id = tab_result["tab"]["tab_id"]
                anchor_pane_id = tab_result["root_pane"]["pane_id"]
                _run(["herdr", "pane", "rename", anchor_pane_id, "Anchor"], check=False)
                node["tab_id"] = tab_id
                node["anchor_pane_id"] = anchor_pane_id
                repaired = True

    if repaired:
        normalized = normalize_workflow(workflow_cfg)
        for n in normalized.get("nodes", []):
            if n["id"] == node["id"]:
                n["tab_id"] = tab_id
                n["anchor_pane_id"] = anchor_pane_id
        for s in normalized.get("stages", []):
            if s["key"] == node["id"]:
                s["tab_id"] = tab_id
                s["anchor_pane_id"] = anchor_pane_id

        if workflow_id:
            save_workflow_config_for(workflow_id, normalized)
        elif project and project.get("workflow_file"):
            _save(project["workflow_file"], normalized)

    return {
        "workspace_id": workspace_id,
        "project_root": project_root,
        "node_id": node["id"],
        "node_label": label,
        "tab_id": tab_id,
        "anchor_pane_id": anchor_pane_id,
        "depends_on": node.get("depends_on", []),
        "node_type": node.get("node_type", "agent"),
        "agent_policy": node.get("agent_policy", {}),
        "purpose": node.get("purpose", ""),
        "default_integration_mode": node.get("default_integration_mode", "none"),
        "default_task_type": node.get("default_task_type", "docs"),
        "required_outputs": node.get("required_outputs", []),
        "rules": node.get("rules", []),
    }
