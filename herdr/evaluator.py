#!/opt/homebrew/bin/python3
"""Herdr Autonomous Evaluation Engine for Agentic Loops.

Implements the 5-element paradigm (Goal, Metrics, Data, Markdown files, Cron):
- Computes 5-dimensional quantitative metric vector:
  (correctness, quality, scope, repro, convergence)
- Parses test runners (pytest, vitest, jest, etc.) and linter/typecheck outputs
- Generates structured Markdown reports (METRICS.md, EVALUATION.md, STATE.md)
"""

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

LOOP_DIR_NAME = ".herdr-loop"


def get_loop_dir(base_dir: Path) -> Path:
    return Path(base_dir) / LOOP_DIR_NAME


def init_loop(
    target_dir: Path,
    goal: str,
    acceptance: str = "",
    test_cmd: str = "",
    lint_cmd: str = "",
    max_iterations: int = 5,
    repro_cmd: str = "",
) -> Path:
    """Initialize .herdr-loop directory structure and contracts."""
    loop_dir = get_loop_dir(target_dir)
    loop_dir.mkdir(parents=True, exist_ok=True)

    # 1. Write GOAL.md
    goal_md = loop_dir / "GOAL.md"
    goal_content = f"""# 任务目标契约 (Node Goal Contract)

- **初始化时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}
- **最大循环轮次**: {max_iterations}

## 核心目标
{goal.strip()}

## 验收标准 (DoD)
{acceptance.strip() or '- [ ] 所有测试 100% 绿灯\n- [ ] 零 Lint / 语法错误'}

## 评估指令配置
- **测试命令**: `{test_cmd or 'pytest'}`
- **代码质量**: `{lint_cmd or 'true'}`
"""
    if repro_cmd:
        goal_content += f"- **靶向复现用例**: `{repro_cmd}`\n"
    goal_md.write_text(goal_content, encoding="utf-8")

    # 2. Write EVALUATOR.sh
    repro_block = ""
    if repro_cmd:
        repro_block = f"""
echo "=== [3/3] RUNNING REPRO CASE ==="
{repro_cmd} > "$LOG_DIR/repro.log" 2>&1
REPRO_EXIT=$?
echo "REPRO_EXIT=$REPRO_EXIT"
"""

    evaluator_sh = loop_dir / "EVALUATOR.sh"
    evaluator_content = f"""#!/usr/bin/env bash
# Herdr Evaluation Runner
# Output streams are parsed into quantitative metrics.

set -o pipefail

ROOT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")/.." && pwd)"
cd "$ROOT_DIR" || exit 1

LOG_DIR="$ROOT_DIR/{LOOP_DIR_NAME}/logs"
mkdir -p "$LOG_DIR"

echo "=== [1/3] RUNNING TESTS ==="
{test_cmd or 'pytest'} > "$LOG_DIR/test.log" 2>&1
TEST_EXIT=$?
echo "TEST_EXIT=$TEST_EXIT"

echo "=== [2/3] RUNNING LINT / QUALITY ==="
{lint_cmd or 'true'} > "$LOG_DIR/lint.log" 2>&1
LINT_EXIT=$?
echo "LINT_EXIT=$LINT_EXIT"
{repro_block}
exit 0
"""
    evaluator_sh.write_text(evaluator_content, encoding="utf-8")
    evaluator_sh.chmod(0o755)

    # 3. Write STATE.md
    state_file = loop_dir / "STATE.md"
    state_file.write_text(f"""# 循环执行状态 (Loop State)

- **iteration**: 0
- **max_iterations**: {max_iterations}
- **status**: initialized
- **last_updated**: {time.strftime('%Y-%m-%d %H:%M:%S')}
- **converged**: false
""", encoding="utf-8")

    # 4. Write initial placeholder METRICS.json
    metrics_json = loop_dir / "METRICS.json"
    init_metrics = MetricVector(
        correctness=0.0,
        quality=0.0,
        scope=100.0,
        repro=0.0 if repro_cmd else 100.0,
        composite_score=0.0,
        has_repro_test=bool(repro_cmd),
    )
    metrics_json.write_text(json.dumps(init_metrics.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    # 5. Write initial METRICS.md and EVALUATION.md
    (loop_dir / "METRICS.md").write_text(render_metrics_markdown(init_metrics, 0, max_iterations), encoding="utf-8")
    (loop_dir / "EVALUATION.md").write_text("# 评估器就绪\n\n请开始执行实现工作。执行后运行 `herdr-loop eval` 进行评分。\n", encoding="utf-8")
    return loop_dir


def read_state(loop_dir: Path) -> dict:
    state_file = loop_dir / "STATE.md"
    if not state_file.exists():
        return {"iteration": 0, "max_iterations": 5, "status": "unknown", "converged": False}
    text = state_file.read_text(encoding="utf-8")
    res = {}
    for line in text.splitlines():
        if line.startswith("- **") and "**:" in line:
            k = line.split("**")[1].strip()
            v = line.split("**:", 1)[1].strip()
            if v.isdigit():
                res[k] = int(v)
            elif v.lower() == "true":
                res[k] = True
            elif v.lower() == "false":
                res[k] = False
            else:
                res[k] = v
    return res


def write_state(loop_dir: Path, iteration: int, max_iter: int, status: str, converged: bool) -> None:
    state_file = loop_dir / "STATE.md"
    state_file.write_text(f"""# 循环执行状态 (Loop State)

- **iteration**: {iteration}
- **max_iterations**: {max_iter}
- **status**: {status}
- **last_updated**: {time.strftime('%Y-%m-%d %H:%M:%S')}
- **converged**: {'true' if converged else 'false'}
""", encoding="utf-8")



@dataclass
class MetricVector:
    correctness: float = 100.0   # 0.0 - 100.0 (test pass rate)
    quality: float = 100.0       # 0.0 - 100.0 (lint, typecheck, static analysis)
    scope: float = 100.0         # 0.0 - 100.0 (boundary adherence, no unneeded edits)
    repro: float = 100.0         # 0.0 or 100.0 (reproduction/blocker test status)
    composite_score: float = 100.0
    passed_tests: int = 0
    total_tests: int = 0
    failing_tests: List[str] = field(default_factory=list)
    lint_errors: int = 0
    type_errors: int = 0
    has_repro_test: bool = False
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def parse_test_output(output: str, exit_code: int) -> Tuple[int, int, List[str]]:
    """Extract passed, total, and failing test names from test output.
    
    Supports pytest, vitest, jest, and generic test runners.
    Returns: (passed_count, total_count, failing_tests_list)
    """
    failing: List[str] = []
    
    # 1. Check Pytest format: "1 failed, 4 passed in 0.15s" (with or without =)
    pytest_match = re.search(r"(?:=+)?\s*([\d\w\s,]+)\s+in\s+[\d\.]+s", output)
    if pytest_match:
        summary_str = pytest_match.group(1)
        passed_m = re.search(r"(\d+)\s+passed", summary_str)
        failed_m = re.search(r"(\d+)\s+failed", summary_str)
        error_m = re.search(r"(\d+)\s+error", summary_str)
        
        if passed_m or failed_m or error_m:
            passed = int(passed_m.group(1)) if passed_m else 0
            failed = int(failed_m.group(1)) if failed_m else 0
            errors = int(error_m.group(1)) if error_m else 0
            total_failed = failed + errors
            total = passed + total_failed
            
            for line in output.splitlines():
                if line.startswith("FAILED ") or line.startswith("ERROR "):
                    parts = line.split(maxsplit=1)
                    if len(parts) > 1:
                        target = parts[1].split(" - ")[0].strip()
                        if target not in failing:
                            failing.append(target)
                        
            return passed, max(total, 1 if exit_code != 0 else 0), failing

    # 2. Check Vitest format: "Tests  1 failed | 11 passed (12)" or "Tests  12 passed (12)"
    vitest_pipe_match = re.search(r"Tests\s+(?:(\d+)\s+failed\s*\|\s*)?(?:(\d+)\s+passed\s*)?\((\d+)\)", output)
    if vitest_pipe_match:
        failed_str, passed_str, total_str = vitest_pipe_match.groups()
        failed = int(failed_str) if failed_str else 0
        passed = int(passed_str) if passed_str else 0
        total = int(total_str) if total_str else (passed + failed)
        
        for line in output.splitlines():
            if "✕" in line or "FAIL " in line:
                cleaned = line.replace("FAIL", "").replace("✕", "").strip()
                if cleaned and cleaned not in failing:
                    failing.append(cleaned)
        return passed, total, failing

    # 3. Check Jest format: "Tests: X failed, Y passed, Z total"
    jest_match = re.search(r"Tests:\s*(?:(\d+)\s+failed,\s*)?(?:(\d+)\s+passed,\s*)?(\d+)\s+total", output)
    if jest_match:
        failed_str, passed_str, total_str = jest_match.groups()
        failed = int(failed_str) if failed_str else 0
        passed = int(passed_str) if passed_str else 0
        total = int(total_str) if total_str else (passed + failed)
        
        for line in output.splitlines():
            if "FAIL " in line or "✕" in line:
                cleaned = line.replace("FAIL", "").replace("✕", "").strip()
                if cleaned and cleaned not in failing:
                    failing.append(cleaned)
        return passed, total, failing

    # 4. Check Python standard unittest format: "Ran X test(s) in Ys"
    unittest_match = re.search(r"Ran\s+(\d+)\s+tests?\s+in\s+[\d\.]+s", output)
    if unittest_match:
        total = int(unittest_match.group(1))
        if exit_code == 0 and ("\nOK" in output or output.endswith("OK")):
            return total, total, []
        
        failed_cnt = 0
        fail_m = re.search(r"failures=(\d+)", output)
        err_m = re.search(r"errors=(\d+)", output)
        if fail_m:
            failed_cnt += int(fail_m.group(1))
        if err_m:
            failed_cnt += int(err_m.group(1))
        if failed_cnt == 0:
            failed_cnt = 1
        
        passed = max(0, total - failed_cnt)
        for line in output.splitlines():
            if line.startswith("FAIL: ") or line.startswith("ERROR: "):
                target = line.split(maxsplit=1)[1].strip()
                if target not in failing:
                    failing.append(target)
        return passed, total, failing

    # 5. Fallback: generic exit code
    if exit_code == 0:
        return 1, 1, []
    else:
        # Extract probable failure line
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        err_msg = lines[-1] if lines else "Process failed with non-zero exit code"
        if err_msg.startswith("FAILED ") or err_msg.startswith("ERROR "):
            parts = err_msg.split(maxsplit=1)
            if len(parts) > 1:
                err_msg = parts[1].split(" - ")[0].strip()
        return 0, 1, [err_msg]


def parse_lint_output(output: str, exit_code: int) -> int:
    """Parse linter / typecheck error count from output."""
    if exit_code == 0:
        return 0
        
    # Check ESLint format: "X problems (Y errors, Z warnings)"
    eslint_match = re.search(r"(\d+)\s+problems?\s*\((\d+)\s+errors?", output)
    if eslint_match:
        return int(eslint_match.group(2))
        
    # Check TypeScript tsc format: "Found X errors"
    tsc_match = re.search(r"Found\s+(\d+)\s+errors?", output)
    if tsc_match:
        return int(tsc_match.group(1))

    # Count lines containing "error:" or "Error:"
    count = 0
    for line in output.splitlines():
        if re.search(r"\b(?:error|Error|ERROR)\b", line) and not re.search(r"\b(?:0 error|0 errors)\b", line):
            count += 1
            
    if count == 0 and exit_code != 0:
        count = 1
    return count


def calculate_metrics(
    test_output: str,
    test_exit_code: int,
    lint_output: str = "",
    lint_exit_code: int = 0,
    type_output: str = "",
    type_exit_code: int = 0,
    repro_output: Optional[str] = None,
    repro_exit_code: Optional[int] = None,
    modified_files: Optional[List[str]] = None,
    allowed_patterns: Optional[List[str]] = None,
) -> MetricVector:
    """Compute the 5-dimensional metric vector from execution outputs."""
    passed, total, failing = parse_test_output(test_output, test_exit_code)
    correctness = (float(passed) / float(total) * 100.0) if total > 0 else (100.0 if test_exit_code == 0 else 0.0)
    
    lint_errs = parse_lint_output(lint_output, lint_exit_code)
    type_errs = parse_lint_output(type_output, type_exit_code)
    
    # Quality penalty: 10 points per lint error, 15 points per type error
    quality_penalty = (lint_errs * 10.0) + (type_errs * 15.0)
    quality = max(0.0, 100.0 - quality_penalty)
    
    # Scope check: Check if files modified exceed allowed patterns
    scope = 100.0
    out_of_bounds = []
    if modified_files and allowed_patterns:
        for f in modified_files:
            matched = any(re.search(pat, f) for pat in allowed_patterns)
            if not matched:
                out_of_bounds.append(f)
        if out_of_bounds:
            # Deduct 15 points per out-of-bounds file
            scope = max(0.0, 100.0 - (len(out_of_bounds) * 15.0))
            
    # Repro test: if present, must pass completely (100.0 or 0.0)
    has_repro = repro_output is not None
    if has_repro:
        repro_val = 100.0 if repro_exit_code == 0 else 0.0
    else:
        repro_val = 100.0

    # Calculate composite score
    if has_repro:
        weights = {"correctness": 0.35, "quality": 0.25, "scope": 0.10, "repro": 0.30}
    else:
        weights = {"correctness": 0.50, "quality": 0.35, "scope": 0.15, "repro": 0.00}
        
    composite = (
        weights["correctness"] * correctness +
        weights["quality"] * quality +
        weights["scope"] * scope +
        weights["repro"] * repro_val
    )
    
    # Absolute zero-defect rule: cannot score 100.0 if any failures exist
    if (failing or lint_errs > 0 or type_errs > 0 or (has_repro and repro_val < 100.0) or out_of_bounds) and composite >= 100.0:
        composite = 95.0

    return MetricVector(
        correctness=round(correctness, 2),
        quality=round(quality, 2),
        scope=round(scope, 2),
        repro=round(repro_val, 2),
        composite_score=round(composite, 2),
        passed_tests=passed,
        total_tests=total,
        failing_tests=failing,
        lint_errors=lint_errs,
        type_errors=type_errs,
        has_repro_test=has_repro,
        details={
            "out_of_bounds_files": out_of_bounds,
            "test_exit_code": test_exit_code,
            "lint_exit_code": lint_exit_code,
            "type_exit_code": type_exit_code,
            "repro_exit_code": repro_exit_code,
        }
    )


def is_converged(metrics: MetricVector) -> bool:
    """True if metrics satisfy complete convergence (DoD fulfilled, 0 defects)."""
    if metrics.composite_score < 99.9:
        return False
    if metrics.failing_tests:
        return False
    if metrics.lint_errors > 0 or metrics.type_errors > 0:
        return False
    if metrics.has_repro_test and metrics.repro < 99.9:
        return False
    return True


def render_metrics_markdown(metrics: MetricVector, iteration: int, max_iterations: int) -> str:
    """Render a human and agent-readable metrics table."""
    status_icon = "🟢 达成目标 (CONVERGED)" if is_converged(metrics) else "🔴 需继续修复 (ITERATION NEEDED)"
    repro_line = f"| 靶向复现用例 (Repro) | `{metrics.repro}%` | `100.0%` | {'✅' if metrics.repro == 100 else '❌'} |" if metrics.has_repro_test else ""
    
    return f"""# 量化评估指标卡 (Metrics Scorecard)

> 轮次: {iteration} / {max_iterations}  
> 状态: {status_icon}  
> 综合适应度得分: **{metrics.composite_score} / 100.0**

## 核心指标明细

| 指标维度 | 当前值 | 目标值 | 判定 |
|---|---|---|---|
| 正确性 (Correctness) | `{metrics.correctness}%` ({metrics.passed_tests}/{metrics.total_tests}) | `100.0%` | {'✅' if metrics.correctness == 100 else '❌'} |
| 代码质量 (Quality) | `{metrics.quality}%` (Lint: {metrics.lint_errors}, Type: {metrics.type_errors}) | `100.0%` | {'✅' if metrics.quality == 100 else '❌'} |
| 边界控制 (Scope) | `{metrics.scope}%` | `100.0%` | {'✅' if metrics.scope == 100 else '❌'} |
{repro_line}

*更新时间: {time.strftime('%Y-%m-%d %H:%M:%S')}*
""".strip()


def render_evaluation_markdown(
    metrics: MetricVector,
    iteration: int,
    max_iterations: int,
    raw_error_snippet: str = ""
) -> str:
    """Render diagnostic evaluation feedback for the pane agent."""
    if is_converged(metrics):
        return f"""# 第 {iteration} 轮评估诊断报告 (Evaluation Succeeded)

✅ **所有指标均已满分达成！**
- 单元/集成测试：全部通过 ({metrics.passed_tests}/{metrics.total_tests})
- 质量/静态扫描：零错误
- 验收标准 (DoD)：完全满足

本轮微循环结束，可以安全提交并完成当前工单。
""".strip()

    failing_list = "\n".join(f"- ❌ `{t}`" for t in metrics.failing_tests) or "- 无单测失败（可能是质量或静态分析未通过）"
    repro_block = ""
    if metrics.has_repro_test and metrics.repro < 99.9:
        repro_block = "\n### 靶向复现用例 (Repro Defect)\n- ❌ 阻断复现用例仍未通过（缺陷未完全解决）\n"
    
    snippet_block = ""
    if raw_error_snippet.strip():
        snippet_block = f"""
## 报错上下文详情
```
{raw_error_snippet.strip()[:2000]}
```
"""

    return f"""# 第 {iteration} 轮评估诊断报告 (Evaluation Feedback)

> **综合得分**: `{metrics.composite_score} / 100.0` (未达到及格线 100.0)  
> **剩余循环轮次**: {max_iterations - iteration}

## 待修复阻断项 (Blockers)

### 1. 失败测试清单
{failing_list}
{repro_block}
### 2. 静态分析与类型检查
- Lint 错误数: `{metrics.lint_errors}`
- 类型检查错误数: `{metrics.type_errors}`

### 3. 越界修改文件 (如有)
{', '.join(f'`{f}`' for f in metrics.details.get('out_of_bounds_files', [])) or '无（边界合规）'}
{snippet_block}
## 智能体行动指南
1. 仔细阅读上方失败用例与报错上下文；
2. 仅修改与当前目标相关的代码文件；
3. 保存代码后，等待评估器重新评分，直至综合得分达到 100.0。
""".strip()
