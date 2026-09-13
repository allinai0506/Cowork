# 通用人机协同底座 · 阶段一：内核调度解耦与控制元语暴露 (Kernel Control Primitives) 演进报告

## 1. 任务背景与目标

根据《北极星架构体系：通用人机协同运行时》规划，通用人机对等协同底座需从“自主闷头闭门推进”重构为“支持外部全量受控”的开放内核。
阶段一核心目标：
1. 实现 pause / resume（工作流与节点级挂起与恢复）；
2. 实现 step（受控单步推进就绪节点，步进后严格保持暂停）；
3. 实现 rollback（基于 DAG 拓扑逆向索引，对目标节点及其全部下游传递闭包原子级联作废任务并重置调度锁）；
4. 实现 force_pass（门禁人工特批强制放行并保留操作人与依据审计记录）；
5. 实现 checkpoint snapshot（工作流定义与任务全生命周期快照落盘与时间旅行恢复）；
6. 向上暴露 CLI（`herdr-factory`）与 REST API（`console`）对等控制界面，并在前端操作底座中挂载交互。

---

## 2. 核心架构与设计实现

### 2.1 控制元语核心库 (`herdr/kernel.py`)
- **零外部依赖**：遵循 RULES.md 极简依赖原则，纯 Python 标准库（`json`, `pathlib`, `time`, `uuid`）；
- **原子持久化安全**：所有快照与状态更新采用 `tmp + replace` 原子写入，杜绝进程突发中断产生损坏文件；
- **拓扑级联计算**：`collect_downstream_nodes` 精准求出下游闭包，回溯时不伤及独立并行分支或上游完成节点。

### 2.2 控制台接口与交互底座 (`console/herdr_factory_console.py`)
- 扩展标准路由：
  - `POST /api/kernel/pause`
  - `POST /api/kernel/resume`
  - `POST /api/kernel/step`
  - `POST /api/kernel/rollback`
  - `POST /api/kernel/force-pass`
  - `POST /api/kernel/checkpoint`
  - `GET /api/kernel/checkpoints`
- 前端在更多操作下拉菜单中挂载交互入口，危险操作（回溯）配设二次警示确认，blocked 任务配设一键强制放行特批弹窗。

### 2.3 命令行一级接口 (`bin/herdr-factory`)
- `herdr-factory step <workflow_id>`
- `herdr-factory rollback <workflow_id> <target_node_id> [--reason REASON]`
- `herdr-factory force-pass <workflow_id> <gate_node_id> [--note NOTE] [--operator OPERATOR]`
- `herdr-factory checkpoint save <workflow_id> [--tag TAG]`
- `herdr-factory checkpoint list <workflow_id>`
- `herdr-factory checkpoint restore <workflow_id> <checkpoint_id>`

---

## 3. 测试与验证证据

### 3.1 自动化测试
| 测试套件 | 测试范围 | 结果 |
|---------|---------|------|
| `tests/test_kernel_control_primitives.py` | 单元覆盖 pause, resume, step, rollback 闭包作废, force_pass, 快照保存与恢复 | 7 PASSED |
| `tests/test_console_kernel_api.py` | REST API 入参校验与响应契约 | 5 PASSED |
| `tests/test_console_frontend_syntax.py` | `node -c` 前端 JS 语法静态编译与无障碍断言 | 8 PASSED |
| **全量回归测试** | 核心调度引擎、状态机、多 Agent 路由、自愈内循环等全部模块 | **284 PASSED (100%)** |

### 3.2 部署验证
- 执行 `./scripts/install-herdr-console.sh` 同步到 `~/.herdr-console`；
- 控制台热载入正常；
- CLI `bin/herdr-factory --help` 与 `bin/herdr-factory checkpoint --help` 解析正常。

---

## 4. 知识沉淀与 Wiki 同步

- **工程教训**：在 `docs/lessons/lessons-learned.md` 追加第 22 章《复杂多 Agent 调度内核的外部受控原则：控制元语与状态快照必须原子解耦，严禁闭门单向推进》；
- **Wiki 演进日志**：在 `wiki/log.md` 记录 `Universal Runtime Phase 1: Kernel Control Primitives & State Snapshots`。
