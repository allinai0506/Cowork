# 基于“五要素”（Goal, Metrics, Data, Markdown, Cron）的智能体自优化循环系统设计与落地方案

## 0. 背景与近期事故日志复盘：总指挥为什么会“卡壳”？

在近期任务（以真实日志 `wf-nexusarchive-54433229-20260913-111049` 及前序 `084418` 为例）中，系统执行标准的单次前向流程（`requirements -> plan -> implementation -> test -> review -> wrapup`）表现良好，但在**审核/测试发现问题需要打回修改**时，系统频繁陷入停滞与死锁。

通过深挖 `controller.out.log`、`sentinel.out.log` 及 `tasks.json`，发现了导致总指挥（Coordinator）与系统卡壳的深层病灶：

### 真实日志复盘剖析

1. **角色认知错位与状态自消除**：
   - 在 `wf-...-111049` 中，`review-01-superadmin-quality` 评审发现实现存在 P1 角色维回归（多角色变体漏判）。
   - 评审 Agent 完成分析并退出后，Sentinel 检测到进程退出，将任务置为 `agent_done`。
   - Controller 提示总指挥对 `review-01` 验收。总指挥发出了 `status=rework` 决策。
   - **致命矛盾**：评审任务本身并没有错，错的是上游实现！但总指挥将评审任务自身设为 `rework`。由于评审 Pane 里的 Agent 进程已结束，Sentinel 在下一个扫描周期（3秒内）看到 Pane 依然空闲，立即又触发了 `rework -> agent_done`（见 `sentinel.out.log:75`）。
   - 随后 Controller 再次催促，总指挥不知所措，最终将 `review-01` 标记为 `completed`（`verdict=None`），导致设计好的门禁机制（`stage_verdict: blocked`）彻底被绕过。

2. **人工调度重载导致死锁与轰炸**：
   - 门禁被绕过后，总指挥只能手动补发 `fix-01-superadmin-role-regression`。
   - 但此时 Controller 正在频繁轮询总指挥 Pane，发现总指挥正在处理其他交互，持续打印了上百条：
     `[COORDINATOR BUSY] status=working task=fix-01-superadmin-role-regression`
   - 总指挥 LLM 上下文被大量的异步事件文本冲刷，迷失在“当前到底是该推进、该验收、该回炉、还是该合并”的状态混乱中。

3. **Pane 内 Agent 缺乏微循环（Inner Loop），单次射后不理（One-shot fire-and-forget）**：
   - 当前进入 Pane 的 Agent（如 Codex、OpenCode）只接收单次 Prompt，执行完几条命令或写完代码就退出了。
   - Agent 自身没有“运行测试 -> 观察度量指标 -> 未达标则自动诊断并修改 -> 再次测试”的闭环能力，导致未自测完备的缺陷直接泄漏到下游评审阶段。

---

## 1. 核心理念与架构总览：五要素闭环法则

用户提出极具洞见的极简哲学：**“Markdown files, cron jobs, goal, metrics, data.”（Markdown 文件、定时任务、目标、指标、数据。）**

我们将这一哲学转化为工程闭环系统，彻底取缔总指挥人工做“传声筒”的脆弱架构，构建**双环智能体自优化系统（Dual-Loop Agentic System）**：

```
       ┌────────────────────────────────────────────────────────────────────────┐
       │                 Outer Loop: 跨阶段回流宏循环 (Controller / Cron)       │
       │                                                                        │
       │  Requirements ──> Plan ──> Implementation ──> Test ──> Review ──> Pass│
       │       ▲                         ▲               │         │            │
       │       │                         │               ▼         ▼            │
       │       │                         └──[Blocked: Repro Data & Blocker.md]  │
       │       └────────────────────────────[Spec Drift: Gap Data & Goal.md]   │
       └────────────────────────────────────────────────────────────────────────┘

       ┌────────────────────────────────────────────────────────────────────────┐
       │                 Inner Loop: Pane 内自主优化微循环 (Agent + Evaluator)  │
       │                                                                        │
       │   ┌──────────┐      ┌───────────┐      ┌───────────┐      ┌─────────┐  │
       │   │  GOAL.md │ ───> │  Generate │ ───> │  Evaluate │ ───> │ METRICS │  │
       │   └──────────┘      │  (Agent)  │      │  (Harness)│      │  .json  │  │
       │        ▲            └───────────┘      └───────────┘      └─────────┘  │
       │        │                                                        │      │
       │        └───────── [Cron Tick / Score < 100: EVALUATION.md] ─────┘      │
       └────────────────────────────────────────────────────────────────────────┘
```

### 五要素的具体语义

| 维度 | 载体 | 核心职责 |
|------|------|----------|
| **Goal (目标)** | `GOAL.md` | 不可动摇的北极星契约：验收标准（DoD）、业务红线、目标指标阈值 |
| **Metrics (指标)** | `METRICS.json` / `METRICS.md` | 确定性的量化评估向量（通过率、缺陷数、覆盖率、复杂度、收敛速率） |
| **Data (数据)** | `tests/`, `fixtures/`, `diff`, `logs` | 评估事实：复现用例、金标测试集、静态扫描 JSON、Git 基准指纹对比 |
| **Markdown (状态与上下文)** | `STATE.md`, `EVALUATION.md`, `HYPOTHESIS.md` | 状态机载体与可读认知记忆，无内存黑盒，断电/重启不丢现场 |
| **Cron jobs (心跳摆钟)** | `herdr-loop` 定时周期任务 | 摆脱对人/总指挥主动催促的依赖，周期性自检、打分、触发修正并推动收敛 |

---

## 2. 自主优化评估系统与五维量化指标体系

智能体循环不能依赖自然语言“感觉差不多了”，必须依赖**确定性的标量与向量计算**。

### 2.1 五维评估向量 (Five-Dimensional Metric Vector)

每个节点与每次循环均输出结构化指标向量 $M = \langle M_{\text{correct}}, M_{\text{quality}}, M_{\text{scope}}, M_{\text{repro}}, M_{\text{conv}} \rangle$：

1. **正确性指标 ($M_{\text{correct}} \in [0, 100]$)**：
   $$M_{\text{correct}} = \frac{N_{\text{passed\_tests}}}{N_{\text{total\_tests}}} \times 100$$
   - 基础单测、集成测试的全量通过率。

2. **代码与架构质量指标 ($M_{\text{quality}} \in [0, 100]$)**：
   $$M_{\text{quality}} = 100 - (\alpha \cdot N_{\text{lint\_err}} + \beta \cdot N_{\text{type\_err}} + \gamma \cdot N_{\text{arch\_violations}})$$
   - 依赖静态分析工具（ESLint, TypeScript `tsc`, flake8, mypy）的机械判分，任何语法或规则报警即扣分至不及格。

3. **变更边界控制指标 ($M_{\text{scope}} \in [0, 100]$)**：
   - 任务变更紧凑度。对比任务声明的修改文件列表与实际 `git diff`，计算越界修改扣分，防止 Agent 自作主张重构非目标代码。

4. **靶向缺陷复现指标 ($M_{\text{repro}} \in \{0, 100\}$)**：
   - 专门用于 Fix-Loop。上游或评审阶段提供的复现脚本/用例：0 表示用例依然失败（未修好），100 表示用例绿灯通过。

5. **收敛速率指标 ($M_{\text{conv}}$)**：
   $$\Delta S_k = S_k - S_{k-1}$$
   - 第 $k$ 次迭代较第 $k-1$ 次迭代的综合得分提升。若连续 2 次迭代 $\Delta S_k \le 0$（打转或负优化），触发自适应降级或切换修复假设。

### 2.2 综合适应度得分 (Composite Fitness Function)

$$S = w_1 M_{\text{correct}} + w_2 M_{\text{quality}} + w_3 M_{\text{scope}} + w_4 M_{\text{repro}}$$

- **门禁及格线**：$S_{\text{pass}} = 100$（硬性零缺陷原则，不允许带着未解决的报错交付）。

---

## 3. 双环运行机制设计

### 3.1 Pane 内微循环机制（Inner Loop）

在 Pane 分配后，Agent 不再裸跑一次，而是置于 `herdr-loop` 的受控沙盒中：

1. **注入契约**：Worker 在装配工作区时，在任务根目录生成 `.herdr-loop/`：
   - `GOAL.md`：当前任务的目标描述与验收标准。
   - `EVALUATOR.sh`：自动化评估脚本（集成测试命令、lint、typecheck）。
   - `STATE.md`：记录当前循环轮次（`iteration: 1`）、状态（`evaluating | generating | converged | exhausted`）。
2. **Cron/Tick 驱动执行**：
   - Pane 内常驻轻量心跳（`herdr-loop tick`）：
   - 执行当前代码的 `EVALUATOR.sh`。
   - 将输出解析为 `METRICS.json` 与 `METRICS.md`。
   - 若 $S < 100$：生成 `EVALUATION.md`（包含具体错误堆栈、失败测试、代码定位），向 Agent 发送指令：“第 k 轮评估得分 65/100，存在以下失败项，请阅读 EVALUATION.md 进行修复并推进代码”。
   - 若 $S == 100$：标记 `STATE.md` 为 `converged`，运行基准快照，提交代码，优雅通知完成。
   - 若 $k \ge \text{max\_loops}$（默认 5）：标记 `exhausted`，冻结现场，输出排查摘要，向外发求助。

### 3.2 跨节点回流宏循环（Outer Loop）

当问题在当前节点内无法解决（例如实现已自测通过，但 Review/Test 阶段基于全局视角的专项对抗测试查出架构隐患或漏判）：

1. **结构化生成 Blocker 与 Repro Data**：
   - 评审/测试节点在打回时，必须产出机器可读的物料（写在 `.herdr-loop/` 中）：
     - `BLOCKER.md`：问题根因、违背的原则、修复要求。
     - `repro_test.*`：**一个可独立运行且必定失败的复现测试用例**（Data 要素！）。
2. **Controller 自动回流（无需总指挥思考）**：
   - Controller 捕获门禁 `blocked` 结论。
   - Controller 自动执行 PR-2 已经做好的原子作废（gate + 下游全部任务置 `superseded`）。
   - Controller 读取 Review 产出的 `repro_test` 与 `BLOCKER.md`，直接合成下一轮实现节点的 `GOAL.md`。
   - 自动在同一分支（`--onto <branch>`）派发或激活目标节点的 Pane，并将 `repro_test` 作为目标节点的及格门禁数据！
   - 目标节点进入 Inner Loop，直到该复现用例跑通、综合得分恢复 100，流程自动重新进入 Test 与 Review。

---

## 4. 文件协议规范 (File Protocols)

所有上下文与状态以纯文本 Markdown 和 JSON 落地在 `.herdr-loop/` 目录中：

### 4.1 `.herdr-loop/GOAL.md`
```markdown
# 任务目标契约 (Node Goal Contract)

- **Node ID**: implementation
- **Workflow ID**: wf-nexusarchive-54433229-20260913-111049
- **Max Iterations**: 5

## 1. 核心目标
修复超管多角色变体漏判问题，确保所有角色代码均通过集中 store 判定。

## 2. 必须满足的硬性门禁 (DoD)
- [ ] 运行 `npm run test:run` 100% 绿灯通过
- [ ] 运行 `npx eslint-rules/tests` 0 报错
- [ ] 针对本次缺陷的复现用例 `tests/repro_superadmin_role.test.ts` 通过

## 3. 约束与边界
- 严禁修改后端通信接口
- 仅允许改动 `src/store/useAuthStore.ts` 及相关 hooks
```

### 4.2 `.herdr-loop/METRICS.json`
```json
{
  "timestamp": "2026-09-13T15:25:00Z",
  "iteration": 2,
  "score": 85.0,
  "metrics": {
    "correctness": 95.2,
    "quality": 100.0,
    "scope": 90.0,
    "repro": 0.0
  },
  "failing_tests": [
    "tests/repro_superadmin_role.test.ts > should handle multi-role array correctly"
  ],
  "lint_errors": 0,
  "type_errors": 0
}
```

### 4.3 `.herdr-loop/EVALUATION.md`
```markdown
# 第 2 轮评估诊断报告 (Iteration 2 Evaluation)

- **综合得分**: 85 / 100 (门禁未通过)
- **状态**: 需修正 (Action Required)

### 失败详情
1. **复现用例失败**: `tests/repro_superadmin_role.test.ts`
   - **错误**: `AssertionError: expected false to be true`
   - **位置**: `src/store/useAuthStore.ts:142`
   - **线索**: 当用户 roles 数组包含 `['auditor', 'SUPER_ADMIN']` 时，大小写不一致导致判断失效。

### 下一步行动指南
请在 `src/store/useAuthStore.ts` 中规范化 role 字符串比较逻辑，保存文件后将自动触发第 3 轮评估。
```

---

## 5. 改造范围与落地规划 (Proposed Changes)

### Component 1: 核心循环工具与执行器 (`herdr-loop`)

#### [NEW] [`bin/herdr-loop`](file:///Users/user/herdr/bin/herdr-loop)
- 循环守护与评估 CLI：
  - `herdr-loop init`: 初始化工作区的 `.herdr-loop/` 上下文。
  - `herdr-loop evaluate`: 运行评估器，收集测试和静态扫描结果，计算得分，落盘 `METRICS.json` 与 `EVALUATION.md`。
  - `herdr-loop tick`: 单步心跳执行，判断是否收敛或超限。
  - `herdr-loop run-daemon`: Pane 内部的守护进程（类似小型 cron），监控文件变动并自驱动循环。

#### [NEW] [`herdr/evaluator.py`](file:///Users/user/herdr/herdr/evaluator.py)
- 通用指标评分引擎：
  - 测试通过率解析器（pytest、jest、vitest、cargo test 通用输出适配器）。
  - Lint / Typecheck 结果解析器。
  - Git Diff 边界计算。

### Component 2: 工位装配与调度升级 (`herdr-worker` & `herdr-controller`)

#### [MODIFY] [`services/herdr-worker.py`](file:///Users/user/herdr/services/herdr-worker.py)
- 在 Clone 工位初始化时，按 Node 模板自动生成 `.herdr-loop/` 结构与基础 `EVALUATOR.sh`。
- 将任务执行命令包装进循环上下文，赋予 Agent 持续感知自身得分的能力。

#### [MODIFY] [`services/herdr-controller.py`](file:///Users/user/herdr/services/herdr-controller.py)
- 消除协调者（Coordinator）在回炉流中的人工阻塞：
  - 当 Gate 判定 `blocked` 时，自动提取 Downstream 写入的 `EVALUATION.md` 与测试用例。
  - 自动向 `retry_node` 派发续接 Task，将回炉指引通过 `GOAL.md` 直接落盘，不再依赖 Coordinator 的文本中转。
  - 彻底解决 `[COORDINATOR BUSY]` 阻塞问题。

#### [MODIFY] [`bin/herdr-task`](file:///Users/user/herdr/bin/herdr-task)
- 增强 `set` 与 `finalize`：
  - 支持直接读取 `.herdr-loop/METRICS.json` 作为验收依据（`herdr-task verify-metrics <task_id>`）。
  - 严禁在得分低于 100 时被设为 `completed`。

---

## 6. 验证计划 (Verification Plan)

### 6.1 自动化测试
- **指标计算与评估单测**：`tests/test_loop_evaluator.py`，测试得分函数、单测结果解析器、收敛判定。
- **Pane 内自修复闭环测试**：`tests/test_inner_loop_convergence.py`，模拟有语法/单测错误的初始代码，验证 Agent 在 3 轮循环内自主将得分由 40 优化至 100 并自动退出。
- **跨阶段自动回炉测试**：`tests/test_outer_loop_flow.py`，模拟评审阶段产出 `repro_test` 和 `blocked`，验证 Controller 零人工介入自动唤醒实现节点并修复成功。

### 6.2 真实场景回放验证
- 针对 `wf-nexusarchive-54433229-20260913-111049` 中“超管角色判定多变体漏判”事件编写回放用例，验证系统能否全自动完成：
  `Review 发现问题 -> 写入 Repro Test -> 自动打回 Implementation -> Inner Loop 自测通过 -> 再次通过 Review -> 正常交付`。
