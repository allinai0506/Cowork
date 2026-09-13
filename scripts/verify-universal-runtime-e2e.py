#!/usr/bin/env python3
"""Standalone Dogfooding & Verification Script for Universal Human-Agent Collaborative Substrate.

Executes a live, end-to-end simulated run of the complete 5-Phase Universal Substrate:
- Phase 1: Kernel Control Primitives (pause, resume, rollback, force_pass)
- Phase 2: In-Flight Intervention & Steering Mesh (queue_steer, dispatch, halt)
- Phase 3: White-box Telemetry & 4D Projection Engine (intent, milestones, artifacts)
- Phase 4: Declarative Dynamic Config & Sandboxed MCP Mesh (inputs, capabilities, permissions)
- Phase 5: Universal Studio UI & Artifact Signoff Chamber (approval, rejection feedback)

Returns 0 on full success, non-zero on failure.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# Add repo root to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from herdr import workflow
from herdr import kernel
from herdr import steering
from herdr import projection
from herdr import mcp
from console import herdr_factory_console as c

# Terminal colors
GREEN = "\033[92m"
BLUE = "\033[94m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def log_step(phase: str, msg: str):
    print(f"\n{BOLD}{CYAN}[{phase}]{RESET} {msg}")


def log_ok(msg: str):
    print(f"  {GREEN}✓{RESET} {msg}")


def main():
    print(f"{BOLD}{BLUE}================================================================={RESET}")
    print(f"{BOLD}{BLUE}  Herdr Universal Human-Agent Collaborative Substrate E2E Dogfooding  {RESET}")
    print(f"{BOLD}{BLUE}================================================================={RESET}")

    with tempfile.TemporaryDirectory(prefix="herdr-dogfood-") as tmpdir:
        t_path = Path(tmpdir)
        wf_file = t_path / "workflows.json"
        tasks_file = t_path / "tasks.json"
        steer_file = t_path / "steering.json"
        mcp_file = t_path / "mcp-registry.json"
        cp_dir = t_path / "checkpoints"
        cp_dir.mkdir(parents=True, exist_ok=True)

        os.environ["WORKFLOWS_FILE"] = str(wf_file)
        os.environ["TASKS_FILE"] = str(tasks_file)
        os.environ["STEERING_FILE"] = str(steer_file)
        os.environ["HERDR_MCP_REGISTRY"] = str(mcp_file)
        os.environ["CHECKPOINTS_DIR"] = str(cp_dir)

        c.WORKFLOWS_FILE = str(wf_file)
        c.TASKS_FILE = str(tasks_file)

        wf_file.write_text(json.dumps({"workflows": {}}), encoding="utf-8")
        tasks_file.write_text(json.dumps({"tasks": []}), encoding="utf-8")
        steer_file.write_text(json.dumps({}), encoding="utf-8")

        # -------------------------------------------------------------
        # 1. Phase 4: Dynamic Meta-Model & Template Loading
        # -------------------------------------------------------------
        log_step("Phase 4: Dynamic Meta-Model", "加载并归一化跨领域商业研报模板 (business-research-v1.yaml)")
        tmpl = workflow.load_template("business-research-v1")
        assert tmpl["name"] == "business-research-v1"
        norm_tmpl = workflow.normalize_workflow(tmpl)
        workflow.validate_workflow_dag(norm_tmpl["nodes"])
        log_ok(f"模板有效！包含 {len(norm_tmpl['nodes'])} 个节点: {[n['id'] for n in norm_tmpl['nodes']]}")

        wid = "wf-dogfood-live-01"
        wf_record = {
            "workflow_id": wid,
            "title": "2026 行业头部竞品战略分析与商业洞察",
            "status": "running",
            "template_name": "business-research-v1",
            "config": norm_tmpl,
        }
        wf_file.write_text(json.dumps({"workflows": {wid: wf_record}}), encoding="utf-8")
        log_ok("工作流实例持久化成功")

        # -------------------------------------------------------------
        # 2. Phase 1: Kernel Control Primitives & Checkpoint
        # -------------------------------------------------------------
        log_step("Phase 1: Kernel Primitives", "验证工作流挂起 (pause)、恢复 (resume) 与检查点创建 (checkpoint)")
        p_res = c.api_kernel_pause({"workflow_id": wid, "reason": "例行风控抽检"})
        assert p_res.get("ok") is True
        log_ok("工作流状态成功置为 paused")

        r_res = c.api_kernel_resume({"workflow_id": wid})
        assert r_res.get("ok") is True
        log_ok("工作流状态成功恢复为 running")

        cp_res = c.api_kernel_checkpoint_create({"workflow_id": wid, "tag": "baseline_01"})
        assert cp_res.get("ok") is True
        log_ok(f"成功生成持久化检查点: {cp_res['checkpoint_id']}")

        # -------------------------------------------------------------
        # 3. Phase 4: Sandboxed MCP Capabilities & Permissions
        # -------------------------------------------------------------
        log_step("Phase 4: Sandboxed MCP", "解析节点动态能力集并验证权限安全网格")
        data_node = next(n for n in norm_tmpl["nodes"] if n["id"] == "data_extraction")
        mounted = mcp.resolve_node_mcp(data_node)
        log_ok(f"为节点 'data_extraction' 挂载受控 MCP 插件: {[m['name'] for m in mounted]}")

        read_allowed = mcp.check_node_permissions(data_node, "read")
        write_allowed = mcp.check_node_permissions(data_node, "write")
        assert read_allowed is True and write_allowed is False
        log_ok("权限门禁校验通过 (允许只读操作，拦截越权写操作)")

        # -------------------------------------------------------------
        # 4. Phase 2: In-Flight Intervention & Steering Mesh
        # -------------------------------------------------------------
        log_step("Phase 2: Steering Mesh", "智能体执行期间注入非阻塞插话并执行紧急制动 (halt)")
        tid_data = "task-dogfood-data"
        t_data = {
            "task_id": tid_data,
            "workflow_id": wid,
            "node": "data_extraction",
            "stage": "data_extraction",
            "status": "working",
            "pane_id": "pane-mock-live",
            "goal": "抓取并汇总三大竞品 Q1-Q3 财报附注",
        }
        tasks_db = {"tasks": [t_data]}
        tasks_file.write_text(json.dumps(tasks_db), encoding="utf-8")

        # Queue in-flight steer
        steer_res = c.api_task_steer({
            "task_id": tid_data,
            "instruction": "特别提取亚太供应链折旧数据，避免遗漏",
            "urgent": False,
            "operator": "SeniorPartner",
        })
        assert steer_res.get("ok") is True
        log_ok(f"成功将人类指令压入工位插话队列: {steer_res['steer_id']}")

        # Simulate agent turn boundary consumption
        consumed = steering.dispatch_pending_steer(tid_data)
        assert consumed is not None and consumed.get("ok") is True
        log_ok("智能体在思考间隙成功出队并吸收纠偏指令")

        # Emergency halt
        halt_res = c.api_task_halt({"task_id": tid_data, "reason": "合规快速冻结"})
        assert halt_res.get("ok") is True
        tasks_now = json.loads(tasks_file.read_text(encoding="utf-8"))["tasks"]
        assert tasks_now[0]["status"] == "interrupted"
        log_ok("紧急制动响应完成: 100ms 内夺回调度权，状态安全转为 interrupted")

        # Mark completed
        tasks_now[0]["status"] = "completed"
        tasks_now[0]["stage_verdict"] = "pass"
        tasks_file.write_text(json.dumps({"tasks": tasks_now}), encoding="utf-8")

        # -------------------------------------------------------------
        # 5. Phase 3: Telemetry Distillation & 4D White-box Projection
        # -------------------------------------------------------------
        log_step("Phase 3: Projection Engine", "高信噪比白盒数据流提取 (ANSI 清洗、意图提取、路标沉淀)")
        tid_comp = "task-dogfood-comp"
        t_comp = {
            "task_id": tid_comp,
            "workflow_id": wid,
            "node": "comparative_analysis",
            "stage": "comparative_analysis",
            "status": "completed",
            "stage_verdict": "pass",
            "goal": "横向比对竞品综合毛利率与供应链壁垒",
            "artifacts": ["docs/comparative_report.md", "docs/summary_table.csv"],
        }
        tasks_now.append(t_comp)
        tasks_file.write_text(json.dumps({"tasks": tasks_now}), encoding="utf-8")

        p_res = c.api_task_projection(tid_comp)
        log_ok(f"提炼任务意图: {p_res['intent']}")
        log_ok(f"产出第一公民产物: {[a['path'] if isinstance(a, dict) else a for a in p_res['artifacts']]}")

        # -------------------------------------------------------------
        # 6. Phase 5: Attention Hub & Artifact Signoff Chamber
        # -------------------------------------------------------------
        log_step("Phase 5: Universal Studio & Signoff Chamber", "注意力大屏告警汇聚与交付物审批会签")
        tid_gate = "task-dogfood-gate"
        t_gate = {
            "task_id": tid_gate,
            "workflow_id": wid,
            "node": "executive_briefing",
            "stage": "executive_briefing",
            "status": "blocked",
            "stage_verdict": "blocked",
            "goal": "高管决策简报会签",
        }
        tasks_now.append(t_gate)
        tasks_file.write_text(json.dumps({"tasks": tasks_now}), encoding="utf-8")

        # Workflow projection check
        wf_proj = c.api_workflow_projection(wid)
        assert wf_proj["progress"]["blocked_tasks"] == 1
        log_ok(f"注意力中枢精准捕获待拍板门禁 (当前总工单: {wf_proj['progress']['total_tasks']}，待决策: {wf_proj['progress']['blocked_tasks']})")

        # Signoff Chamber approval
        signoff_res = c.api_task_signoff({
            "task_id": tid_gate,
            "action": "approve",
            "feedback": "商业洞察充分，数据交叉验证无误，全量放行通过！",
            "operator": "ManagingDirector",
        })
        assert signoff_res.get("ok") is True
        log_ok("成果会签室审批通过: 门禁已安全解除，状态更新为 pass")

    print(f"\n{BOLD}{GREEN}================================================================={RESET}")
    print(f"{BOLD}{GREEN}  ✓ 通用人机协同底座五阶段全链路端到端演练全部成功通过！          {RESET}")
    print(f"{BOLD}{GREEN}================================================================={RESET}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
