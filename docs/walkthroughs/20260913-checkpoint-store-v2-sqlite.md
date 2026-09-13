# Checkpoint Store V2: 嵌入式 SQLite 状态引擎、图谱谱系追踪与时间旅行分叉

> **交付报告与演进归档**  
> 日期：2026-09-13  
> 分支：`feat/checkpoint-store-v2-sqlite`  
> 涉及模块：[`herdr/state_db.py`](file:///Users/user/herdr/herdr/state_db.py)、[`herdr/kernel.py`](file:///Users/user/herdr/herdr/kernel.py)、[`bin/herdr-task`](file:///Users/user/herdr/bin/herdr-task)、[`tests/test_state_db_v2.py`](file:///Users/user/herdr/tests/test_state_db_v2.py)

---

## 1. 任务背景与核心动因

在「通用人机协同运行时」北极星架构体系（Universal Collaborative Runtime）中，状态持久化与状态机回溯是支撑长时间自主协同与安全试错的底层支柱。

在阶段一完成的初步 Checkpoint 快照机制中，状态被序列化为分散的 JSON 文件（`~/.herdr-controller/checkpoints/<wf_id>/<cp_id>.json`）。随着多智能体并发、长时间多轮推理、频繁插话介入和成果会签驳回回滚的增加，文件级存储逐渐显露出局限性：
1. **跨实体原子性难以保证**：工作流元数据（`workflows.json`）、工位任务状态（`tasks.json`）、干预队列与检查点快照分散存储，难以在单事务中实现完全原子的多表写入与状态还原；
2. **缺乏状态图谱与谱系溯源**：检查点彼此孤立，缺乏父级指针与有向演进谱系图（Lineage Graph），无法可视化追溯“这个状态是由哪一次干预或哪个上游检查点派生而来”；
3. **不支持时间旅行分叉 (Time-Travel Branching)**：当流水线行进至某关键节点时，人类或总指挥希望在保留当前真实进展的同时，分叉派生出一条独立的实验性分支进行探索性测试，而在旧架构下只能手动重新复制或覆盖原有流程。

为了彻底解决上述痛点，本里程碑落地 **Checkpoint Store V2**：基于 Python 标准库 `sqlite3` 构建嵌入式高性能状态引擎，提供单事务原子快照、图谱谱系追踪与时间旅行分叉，并保持对既有 V1 JSON 存储的 100% 透明向后兼容。

---

## 2. 架构设计与核心实现

### 2.1 嵌入式状态引擎核心 (`herdr/state_db.py`)

遵循极简哲学（Ponytail Principle），系统零引入任何三方 ORM 或复杂数据库依赖，直接基于 Python 标准库 `sqlite3` 实现高性能嵌入式引擎：
- **物理存储位置**：`~/.herdr-controller/state.db`（可由环境变量 `HERDR_STATE_DB` 重定向）；
- **WAL 并发模式**：初始化自动启用 `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000; PRAGMA foreign_keys=ON;`，实现单写多读高并发与崩溃自愈；
- **四大数据表模型**：
  1. `workflows`：`id` (PK), `project`, `status`, `title`, `spec_yaml`, `created_at`, `updated_at`, `meta_json`；
  2. `tasks`：`id` (PK), `workflow_id` (Index), `node_id`, `status`, `goal`, `agent_kind`, `created_at`, `updated_at`, `meta_json`；
  3. `checkpoints`：`id` (PK), `workflow_id` (Index), `parent_id` (Index, 谱系父节点), `label`, `dag_snapshot`, `state_vector`, `created_at`, `meta_json`；
  4. `events`：`id` (Auto PK), `workflow_id`, `task_id`, `event_type`, `payload`, `created_at`。

### 2.2 连接复用与嵌套事务防锁契约

在早期原型中，嵌套调用常规持久化方法会导致开启新连接写未提交事务库，进而引发 `database is locked`。
`state_db.py` 严格规范了**事务连接透传契约**：
```python
def save_workflow(wf: dict, conn: Optional[sqlite3.Connection] = None) -> None:
    # 若外层传入 conn，则直接复用外层事务连接，不自动 commit/close
    ...
```
在 `restore_checkpoint`、`fork_workflow_from_checkpoint` 和 `migrate_v1_to_v2` 等高级元语中，统一在外层 `with conn: ...` 下将连接深度透传给 `save_workflow` 与 `save_task`，确保 100% 单事务原子执行且零死锁风险。

### 2.3 图谱谱系追踪与时间旅行分叉 (Time-Travel Branching)

- **谱系追踪 (`get_checkpoint_lineage`)**：沿 `parent_id` 指针向前回溯，输出完整的祖先链（Ancestry Lineage Chain），形成 DAG 演进历史；
- **时间旅行分叉 (`fork_workflow_from_checkpoint`)**：
  - 从历史快照中的 `dag_snapshot` 提取工作流拓扑与任务初始规格；
  - 赋予全新衍生的工作流 ID（如 `wf-<project>-fork-...`）与标题；
  - 精准清除历史阶段锁（`stage_locks`）与残留物理工位绑定，将未就绪任务安全重置为初始待派发态；
  - 在检查点表中为分叉记录创建根快照，并将其 `parent_id` 指向源快照，完成谱系交接。

### 2.4 无损双向平滑迁移 (`migrate_v1_to_v2`)

为了让存量用户与既有工作流平滑过渡，实现幂等且无损的迁移工具：
- 扫描 `~/.herdr-controller/workflows.json` 与 `tasks.json`；
- 扫描 `~/.herdr-controller/checkpoints/*/*.json` 存量快照；
- 在单个 SQLite 事务中完成批量 Upsert，返回精准导入计数统计，零数据丢失。

### 2.5 内核透明双写与平滑降级 (`herdr/kernel.py`)

在调度内核控制层中无缝桥接 `state_db`：
1. **双写 ID 归一**：`create_checkpoint` 由上层统筹生成统一 UUID，同时向 JSON 文件与 SQLite 库落盘，确保两层 ID 1:1 精确对齐；
2. **读路径加速与平滑回退**：`get_checkpoint` 与 `list_checkpoints` 优先命中 SQLite 毫秒级索引，若 SQLite 未就绪或记录不存在，透明回退至磁盘 JSON 文件读取；
3. **还原双向一致**：`restore_checkpoint` 在完成 SQLite 恢复后同步更新宿主磁盘 `workflows.json` 与 `tasks.json`，确保老版工具与 Controller 观察到的状态完全一致；
4. **扩展控制原语**：暴露 `kernel.fork_workflow_from_checkpoint`。

### 2.6 CLI 命令行工具扩展 (`bin/herdr-task`)

在 `herdr-task` 中增加 Checkpoint V2 管理命令体系：
- `herdr-task checkpoint-create <workflow_id> [--label ...] [--parent ...]`
- `herdr-task checkpoint-list <workflow_id> [--json]`
- `herdr-task checkpoint-restore <checkpoint_id>`
- `herdr-task checkpoint-fork <checkpoint_id> [--new-workflow-id ...] [--title ...] [--json]`

---

## 3. 自动化验证与测试证据

### 3.1 专用测试套件 (`tests/test_state_db_v2.py`)

编写了 9 大维度的综合测试，覆盖 SQLite 状态引擎核心、并发事务、图谱分叉与 CLI 集成：
1. `test_schema_initialization_and_wal_mode`：验证表结构自动生成与 WAL 模式开启；
2. `test_workflow_and_task_upsert`：验证工作流与任务实体的幂等插入与更新；
3. `test_create_and_get_checkpoint`：验证检查点保存、字段序列化与提取；
4. `test_atomic_restore_checkpoint`：验证恢复检查点时的工作流与任务状态单事务原子覆盖；
5. `test_fork_workflow_from_checkpoint`：验证时间旅行分叉、新工作流衍生、阶段锁清除与谱系关联；
6. `test_checkpoint_lineage_graph`：验证多代快照谱系追溯链提取；
7. `test_migrate_v1_to_v2`：验证从 V1 JSON 文件结构批量迁移至 SQLite 的幂等性与数据完整性；
8. `test_kernel_state_db_bridge_integration`：验证 `herdr/kernel.py` 的双写、ID 对齐与透明桥接；
9. `test_cli_checkpoint_subcommands`：验证 `herdr-task` 命令行工具全部新增子命令的执行与 JSON 格式化输出。

### 3.2 测试执行结果

```bash
/opt/homebrew/bin/pytest tests/test_state_db_v2.py -v
============================= test session starts ==============================
collected 9 items

tests/test_state_db_v2.py::test_schema_initialization_and_wal_mode PASSED [ 11%]
tests/test_state_db_v2.py::test_workflow_and_task_upsert PASSED           [ 22%]
tests/test_state_db_v2.py::test_create_and_get_checkpoint PASSED         [ 33%]
tests/test_state_db_v2.py::test_atomic_restore_checkpoint PASSED         [ 44%]
tests/test_state_db_v2.py::test_fork_workflow_from_checkpoint PASSED      [ 55%]
tests/test_state_db_v2.py::test_checkpoint_lineage_graph PASSED          [ 66%]
tests/test_state_db_v2.py::test_migrate_v1_to_v2 PASSED                   [ 77%]
tests/test_state_db_v2.py::test_kernel_state_db_bridge_integration PASSED [ 88%]
tests/test_state_db_v2.py::test_cli_checkpoint_subcommands PASSED         [100%]

============================== 9 passed in 0.28s ===============================
```

### 3.3 全仓自动化回归验证

```bash
/opt/homebrew/bin/pytest
============================= test session starts ==============================
collected 330 items

... [全量 330 项测试用例 100% 通过] ...
============================= 330 passed in 8.07s ==============================
```

同时，针对五阶段通用人机协同底座端到端演练工具进行实操验证：
```bash
python3 scripts/verify-universal-runtime-e2e.py
======================================================================
HERDR UNIVERSAL RUNTIME E2E DOGFOODING VERIFICATION
======================================================================
[PHASE 1] Dynamic Model & MCP Capability Mesh ... [OK]
[PHASE 2] Kernel Control Primitives ... [OK]
[PHASE 3] In-Flight Steering & Safe Halt ... [OK]
[PHASE 4] Telemetry Distillation & White-box Projection ... [OK]
[PHASE 5] Artifact Signoff Chamber ... [OK]
[PHASE 6] Checkpoint & Disaster Recovery ... [OK]
======================================================================
ALL PHASES OF UNIVERSAL RUNTIME SUBSTRATE VERIFIED SUCCESSFULLY!
======================================================================
```

---

## 4. 沉淀工程教训与规范

本次里程碑沉淀了通用工程教训 **§28**（已更新至 [`docs/lessons/lessons-learned.md`](file:///Users/user/herdr/docs/lessons/lessons-learned.md)）：
- **连接复用规避嵌套事务死锁**：在开启独占事务时，所有被调用写操作必须支持 `conn: Optional[sqlite3.Connection]` 外部连接透传；
- **存储双写 ID 归一**：跨介质持久化必须由单一源头确定第一公民业务实体 ID，严禁分头生成 UUID 导致裂脑；
- **时间旅行分叉需深度重置衍生状态**：分叉派生新执行分支时必须彻底清除旧阶段锁与物理工位绑定，防止调度器幽灵锁死。

---

## 5. 归档结论与后续演进

Checkpoint Store V2 为 Herdr 从基于散落文件的单向推进调度器演进为**支持单事务原子快照、全图谱谱系追溯与时间旅行分叉的高可用协同状态引擎**奠定了工业级数据底座。后续将支持控制台可视化时间旅行树状图分支与一键对比查看。
