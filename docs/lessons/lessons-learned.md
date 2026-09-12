# 通用工程教训与复盘 (Lessons Learned)

> 本文件记录跨模块、可复用的核心工程教训与 SOP，按 session 追加章节。
>
> **编写纪律**：
> 1. 只记录具备通用指导意义的教训，不记录一次性单点业务逻辑；
> 2. 必须遵循四段式结构（问题背景 → 经验教训表格 → 操作规范 → 验证命令/证据）；
> 3. 每条教训必须有真实日志、PR 或代码证据，禁止虚构推测；
> 4. 同一类问题复发 2 次以上，必须推动升格为自动化门禁（pre-push 脚本 / pytest 架构测试 / AGENTS.md 底线）；
> 5. 已被新架构或全量门禁覆盖的历史单点内容，定期归档至 [`lessons-archive.md`](./lessons-archive.md)。

---

## 生命周期闭环

```
真实事故 / 复杂排查
       │
       ▼
1. 单点 RCA 根因分析 (root-cause-analysis-*.md)
       │ 提炼出跨模块、通用性的工程教训
       ▼
2. 沉淀入册 (本文件追加章节)
       │ 同类教训复发 2 次以上
       ▼
3. 规则固化 (pre-push 门禁脚本 / pytest 架构测试 / AGENTS.md 底线)
       │ 已有强门禁覆盖
       ▼
4. 归档瘦身 (单点过时记录移入 lessons-archive.md)
```

---

## 1. LaunchAgent 进程热重载陷阱

### 问题背景

多次出现修改 `services/herdr-controller.py` 后，后台调度行为仍是旧逻辑，排查耗时严重。
根因是 LaunchAgent 进程常驻内存，不会自动热加载 Python 源码。
关联坑点：`RULES.md §4 坑点1`，Git Commit `16025ac`（fix(task): propagate next_stage）。

### 经验教训

| 问题 | 教训 | 规范 |
|------|------|------|
| 修改源码后直接在终端重启进程 | LaunchAgent 与直接 python3 启动会发生 Socket / 状态文件冲突 | 必须用 `launchctl kickstart -k` 热重载，严禁裸启 |
| 修改后未验证进程 PID 是否更新 | 代码更新成功不等于运行中进程已切换 | 热重载后必须用 `launchctl list | grep herdr` 确认 PID 变化 |
| 日志仍是旧逻辑输出 | 日志时间戳是判定"进程是否已切换"的铁证 | 重载后检查 `~/Library/Logs/herdr/*.log` 中的启动时间戳 |

### 操作规范（已固化到 `RULES.md §4 坑点1`、`CLAUDE.md`）

1. **修改 `services/` 任意文件后**：必须执行热重载命令，不得跳过。
   ```bash
   launchctl kickstart -k gui/$(id -u)/com.user.herdr-controller
   ```
2. **验证进程已切换**：对比热重载前后的 PID。
3. **禁止操作**：`python3 services/herdr-controller.py` 直接前台运行（Socket 冲突）。

### 验证命令 / 守护测试

```bash
# 验证热重载后进程 PID 已更新
launchctl list | grep herdr
# 期望：PID 列非空且与重载前不同，ExitStatus 为 0

# 验证无孤儿进程
pgrep -a python3 | grep herdr
# 期望：只有一个 herdr-controller 进程
```

### 相关文档 / 关联证据

- `RULES.md §4 坑点1` — 已固化的操作红线
- `CLAUDE.md §服务管理` — 快速命令参考
- `docs/operations/service-management.md` — 完整 LaunchAgent 运维手册

---

## 2. CoW Clone 验收假象：裸 git status 不可信

### 问题背景

在 CoW Clone 任务目录中使用 `git status`，显示大量"未提交改动"，
误判为 Agent 当前 session 的产出，导致合并时混入主干的未提交变更。
关联坑点：`RULES.md §4 坑点2`。

### 经验教训

| 问题 | 教训 | 规范 |
|------|------|------|
| CoW Clone 创建时继承了主干未提交文件 | `git status` 无法区分"任务前存量"与"本次产出" | 必须用 `herdr-task verify-baseline` 以快照为准 |
| Agent 直接 `git add .` 提交了不相关文件 | 验收必须锁定真实产出边界，不能靠人工肉眼区分 | PR 合并前强制执行 baseline 验证 |

### 操作规范（已固化到 `RULES.md §4 坑点2`）

1. **严禁裸 `git status`**：CoW Clone 目录中禁止以此判定任务产出。
2. **必须使用**：
   ```bash
   ./bin/herdr-task verify-baseline <task-id>
   ```
   只有 `TASK_CHANGED` 下列出的文件，才是当前任务的真实产出。
3. **Agent 提交前**：必须 baseline 验证通过，再执行 `git add`。

### 验证命令 / 守护测试

```bash
./bin/herdr-task verify-baseline <task-id>
# 期望：输出 TASK_CHANGED 文件列表，无 UNEXPECTED_DIFF 警告
```

### 相关文档 / 关联证据

- `RULES.md §4 坑点2` — 已固化红线
- `bin/herdr-task` — verify-baseline 实现

---

## 3. Stage-Advance 竞态：并发节点同时推进导致状态不一致

### 问题背景

DAG 多节点并发完成时，`herdr-controller.py` 中的 stage-advance 逻辑发生竞态：
两个 worker 同时触发 stage 推进，导致部分节点被重复派发或跳过。
关联修复：Git Commit `16025ac`（fix: propagate next_stage）、
Commit `53ff474`（feat: DAG controller refactor）。

### 经验教训

| 问题 | 教训 | 规范 |
|------|------|------|
| stage-advance 未做幂等保护 | 并发调度下无锁的状态写操作必然产生竞态 | stage 推进必须加文件锁 + 幂等检查（已推进则跳过）|
| 状态文件直接 JSON 覆盖写 | 覆盖写在并发下是非原子操作 | 状态更新必须走原子 rename（write-tmp → rename）|
| 缺少并发回归测试 | 竞态 bug 在单线程测试中不可见 | 必须有 `--parallel` 参数的并发回归测试覆盖 |

### 操作规范（已固化到 `services/herdr-controller.py`）

1. **所有 stage-advance 路径**：必须持有 `workflow_state.lock` 文件锁再读写状态。
2. **状态文件写入**：使用 `write-tmp → os.replace` 原子写，禁止直接覆盖。
3. **幂等保护**：写前检查当前 stage，已推进则直接返回，不重复执行。

### 验证命令 / 守护测试

```bash
# 运行 stage-advance 与 supersede 回归测试
pytest tests/test_stage_advance_and_supersede.py -v
# 期望：所有用例 PASSED

# 并发压力验证（如有 parallel fixture）
pytest tests/ -v --tb=short
```

### 相关文档 / 关联证据

- `tests/test_stage_advance_and_supersede.py` — 回归测试（Commit `53ff474`）
- `services/herdr-controller.py` — 调度核心实现
- `wiki/dag-workflow-engine.md` — DAG 调度算法文档

---

## 4. 任务派发通知静默：Agent 无法感知任务到达

### 问题背景

早期 `herdr-task` 在派发任务后没有 macOS 通知，Agent 在另一个 Tab 工作时
无法感知新任务到达，导致任务在 READY 状态停留过久。
关联实现：Commit `53ff474` 新增 `herdr-notifier.py` 集成。

### 经验教训

| 问题 | 教训 | 规范 |
|------|------|------|
| 派发动作无任何外部信号 | 多 Agent 并发场景下"任务等待"是隐性阻断源 | 关键状态变更（派发/完成/失败）必须有通知钩子 |
| 依赖 Agent 主动轮询感知 | 轮询带来延迟且消耗上下文 | 改为 push 式：派发即通知，完成即广播 |

### 操作规范（已固化到 `bin/herdr-task`、`services/herdr-notifier.py`）

1. **任务派发后**：`herdr-task` 自动调用 `herdr-notifier.py` 发送 macOS 通知。
2. **通知内容**：包含 task-id、node label、目标 Agent 类型。
3. **新增状态变更点**：必须评估是否需要补充通知钩子。

### 验证命令 / 守护测试

```bash
# 派发一个测试任务，验证通知是否弹出
./bin/herdr-task dispatch <workflow-id> <node-label> --dry-run
# 期望：终端输出 "Notification sent" 且 macOS 弹出通知横幅
```

### 相关文档 / 关联证据

- `services/herdr-notifier.py` — 通知服务实现
- `bin/herdr-task` — 派发集成点
- `wiki/task-lifecycle.md` — 任务生命周期状态机

---

## 5. Workflow 启动竞态：阶段事件先到、需求正文丢失

### 问题背景

Factory Console 启动 Workflow 时，`herdr-factory` 先写入 Workflow Registry；Controller
周期扫描到新 Workflow 后立即发送 `start → requirements`，而需求正文仍只存在于
`herdr-factory` 的进程参数中，尚未进入 Registry 或总指挥 Pane。Deep Preflight 完成后，
原实现再尝试直接向同一个总指挥 Pane 注入需求，造成 Controller 与 Factory 双写同一 Pane
的竞态。实际证据是 Workflow `wf-nexusarchive-54433229-20260912-194638` 已进入
`requirements`、Task 数为 0，总指挥 Pane 处于 working 但不知道具体需求。

### 经验教训

| 问题 | 教训 | 规范 |
|------|------|------|
| 需求正文只存在于启动进程参数 | Workflow ID、阶段状态和用户需求必须属于同一个持久化启动记录 | Registry 必须保存 `requirement` |
| 注册即触发 Controller 推进 | “已注册”不等于“可派发” | 用 `startup_ready` 门闩隔离注册、预检和首次派发 |
| Factory 与 Controller 同时写总指挥 Pane | 同一会话不能有两个未协调的消息生产者 | Controller 作为首次节点消息的唯一投递者 |
| 队列事件可能早于状态修复进入内存队列 | 只在入队处检查状态不够 | 队列消费者也必须重新检查启动门闩 |
| Dashboard 保留旧 Workflow ID | 当前选中对象和最新 Job 对象可能分离 | 启动成功后必须用 Job 返回的 Workflow ID 更新前端上下文 |

### 操作规范

1. 启动时先写入 `requirement` 和 `startup_ready=false`。
2. Deep Preflight、固定 Agent 校验和策略写入全部完成后，才设置 `startup_ready=true`。
3. Controller 只消费 `startup_ready=true` 的启动记录，并从 Registry 组装总指挥消息。
4. 队列消费者再次检查启动门闩；旧队列事件不能绕过启动协议。
5. 发生中断恢复时，优先检查 Workflow Registry、`stage-state.json`、Task Registry 和
   总指挥 Pane 四层状态，不要只看单个 HTTP 返回码。

### 验证命令 / 证据

```bash
python3 -m unittest tests/test_workflow_start_sync.py
python3 -m unittest discover -s tests -p 'test_*.py'
./bin/herdr-task stage-status wf-nexusarchive-54433229-20260912-194638 requirements
herdr agent get wA:p1
herdr pane read wA:p1 --source visible
```

实际修复证据：Controller 日志出现 `STARTUP WAIT`，预检完成后 Registry 的
`startup_ready` 变为 `true`；清理当前 Workflow 的 requirements 锁并重新评估后，
`wA:p1` 标题变为“零号病人 Bug 责任链追溯脚本需求分析”。回归测试覆盖 Registry
需求持久化、启动门闩、Factory 不再直接投递和队列消费者二次检查。

---

## 6. Agent 负载与预检状态的误算与口径不一致

### 问题背景

控制台看板与 Agent 路由在计算 Agent 负载时，此前将 `ACTIVE` 状态集合设定为了包含 `completed`、`committed`、`integrated`、`cleanup_ready` 等已终结/后置状态。当某一 Agent（如系统未安装的 `codex`）在历史 Workflow 中曾被分配过任务且任务已完成时，在看板上仍会被误算为 `负载 7`。同时，控制台浅层预检在判断二进制和认证路径时使用了过简逻辑，导致控制台看板状态与真实探针存在偏差。

### 经验教训

| 问题 | 教训 | 规范 |
|------|------|------|
| 把已完成的任务统计为 Agent 负载 | Task 终结状态（completed, integrated等）不代表 Agent 仍处于占用状态 | Agent 负载计算只计入处于在途状态（pending, dispatched, working, blocked, agent_done, rework）的 Task |
| 未安装的 Agent 却显示历史负载 | 已终结任务不应膨胀未安装 Agent 的在线负载 | 负载定义统一收窄为运行中/在途状态 |
| 控制台浅层预检二进制名与认证路径缺失 | 模块间二进制名（如 qodercli 对应 qodercn）和认证路径必须保持一致 | 控制台与 preflight 探测字典保持单点事实来源 |

### 操作规范

1. `_active_agent_loads()` 及 `agent_loads()` 必须统一只包含在途任务状态 `IN_FLIGHT_STATUSES`。
2. 控制台与 `preflight.py` 共享 `AGENT_BINARIES` 与 `AUTH_HINTS` 字典判定。
3. 修改控制台源码 `console/herdr_factory_console.py` 后必须运行 `scripts/install-herdr-console.sh` 热同步到 `~/.herdr-console`。

### 验证命令 / 证据

```bash
pytest tests/test_agent_router_loads.py
bash scripts/install-herdr-console.sh
```

---

## 7. Superseded 任务统计口径漂移：同一语义多处手写必然漏改

### 问题背景

`herdr-task launch --supersedes` 将 plan-t2 取代为 plan-t2-rev 后，Workflow 实际早已全部完成
（`stage-status` 判 completed、controller 判 complete、DAG 正常推进到 13/13），但运维驾驶舱的
节点卡片统计把 superseded 计入分母却不计入 completed，节点落到 `pending`；Workflow 详情页的
`stage_summary` 则因 superseded 不在任何状态集合里而落到 `mixed`，UI 渲染为"处理中"。
同一语义（排除被取代任务）在仓库里有 4 处独立手写实现：`is_node_complete`
（services/herdr-controller.py）、`node_status`（bin/herdr-task）、`_node_task_status_counts`
（bin/herdr-task）、`stage_summary`（console），supersede 特性落地时只同步了前两处。

### 经验教训

| 问题 | 教训 | 规范 |
|------|------|------|
| 节点卡片把 superseded 计入分母 | 展示聚合层必须与调度判定层使用同一谓词，否则 UI 与事实分裂 | 统一谓词：`status == "superseded" or superseded_by 存在` |
| console stage_summary 落到 mixed | 状态枚举扩容（新增 superseded）时，所有 if/else 链都要重新审视 else 分支 | 新增终态后，聚合函数必须显式处理或过滤，不允许落入兜底分支 |
| 口径类缺陷第二次复发 | 这是 lessons 第 6 条（Agent 负载口径）之后的同类问题 | 已按纪律升格为 pytest 门禁：`TestOpsCardParity` 钉死卡片与 `is_node_complete` 的一致性 |

### 操作规范

1. 涉及"被取代任务"的任何统计，统一复用组合谓词 `status == "superseded" or task.get("superseded_by")`，
   与 `is_node_complete` / `node_status` 逐字一致；修改任一处必须同步其余处。
2. `_node_task_status_counts` 输出独立的 `superseded` 计数桶，节点/工作流级 `total` 只含存活任务；
   全退役节点用 `superseded` 状态展示，不得伪装成 `empty` 或 `pending`。
3. console `stage_summary` 的 `count` 为存活任务数，`tasks` 列表保留全部记录以维持退役任务可见性。
4. 为口径一致性新增 pytest 门禁后，任何新增统计消费方（新视图/新脚本）都应补对应 parity 用例。

### 验证命令 / 证据

```bash
pytest tests/test_herdr_task_ops_center.py tests/test_stage_advance_and_supersede.py tests/test_console_stage_summary.py
bash scripts/install-herdr-console.sh
curl -s "http://127.0.0.1:8765/api/ops-center?workflow_id=wf-nexusarchive-54433229-20260912-194638"
```

实际修复证据：`/api/ops-center` 返回 plan 节点 `total:2 completed:2 superseded:1 status:completed`，
`/api/workflow` 全部 stage 为 `cleaned`（修复前为 `mixed`→"处理中"）；
drilldown 从最老的 plan-t1 变为权威的 plan-t2-rev。
数据修补记录：plan-t2 已归一为 cleaned（备份 `~/.herdr-controller/backups/tasks.json.bak-20260912-230513`）。
