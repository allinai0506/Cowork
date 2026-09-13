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

## 8. 已装 Agent 被误判"未安装"：`shutil.which` 依赖服务进程 PATH，且映射三处手写

### 问题背景

共事工厂控制台"执行者阵容"把 codex/claude/qodercli/agy 显示为"未安装"，但四者实际已装
（volta、`~/.local/bin`、`~/.qoder-cn/entry`）。前一次修复（§6，commit 7d6dc5a）只修正了
二进制名映射（qodercli→qodercn）与认证提示，探测仍走裸 `shutil.which`。而 console 与
controller 均以 LaunchAgent 常驻，plist PATH 精简为
`/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin`，不含用户级安装目录——
这正是 opencode/pi（homebrew）显示正常、其余四个误判的原因。同一探测语义当时在仓库里有
3 处独立实现：`console/herdr_factory_console.py:preflight`、`herdr/preflight.py:inspect`、
`herdr/deep_preflight.py:resolve_binary`（第三处有 zsh 兜底但硬编码目录漏了 volta）。

### 经验教训

| 问题 | 教训 | 规范 |
|------|------|------|
| `shutil.which` 在 LaunchAgent 里漏判 | 服务进程 PATH ≠ 用户 shell PATH；`~/.zshrc` 补的 PATH 对常驻服务不可见 | 二进制解析不得裸依赖进程 PATH，必须有目录兜底或登录 shell 兜底 |
| AGENT_BINARIES 映射 3 处手写 | 与 §6/§7 同类：同一语义多处手写必然漂移（qodercli 映射 bug 即由此而来） | 映射与解析收敛到 `herdr/agent_binary.py` 单一事实来源，消费方只 import |
| 修复"看起来改了"但问题复现 | 第一次修复只覆盖了名字映射这一层，未追问 `which` 本身的适用边界 | 修 bug 时先完整走一遍数据链路（UI 字段 → 判定函数 → 执行环境），确认根因层而不是症状层 |

### 操作规范

1. 任何需要定位 Agent CLI 的代码，一律 `from herdr.agent_binary import resolve_agent_binary`；
   解析顺序：`shutil.which` → `EXTRA_BIN_DIRS`（`~/.local/bin`、`~/.volta/bin`、`~/.qoder-cn/entry`、homebrew）→ 登录 shell `command -v`。
2. 新增 Agent 注册入口收敛到 `herdr/agent_binary.py:AGENT_BINARIES`；
   `herdr/preflight.py` 只维护 `KNOWN_AGENTS`/`AUTH_HINTS`/`VERSION_ARGS`，`deep_preflight.py` 只维护 `AUTH_HINTS`/错误模式。
3. 给 LaunchAgent 服务写依赖用户环境的功能前，先看 `~/Library/LaunchAgents/com.user.*.plist` 的
   `EnvironmentVariables.PATH`；需要用户 PATH 的逻辑放代码兜底，不要依赖改 plist。
4. 排查"服务里不对、终端里正常"类问题时，第一步用
   `env -i HOME=$HOME PATH=<plist PATH> <python> -c ...` 复现服务环境，再谈代码。

### 验证命令 / 证据

```bash
/opt/homebrew/bin/pytest tests/test_agent_binary_resolution.py tests/test_console_agent_roster.py
bash scripts/install-herdr-console.sh
curl -s "http://127.0.0.1:8765/api/project?id=nexusarchive-54433229" | python3 -c "import json,sys; [print(a['agent'],a['status'],a['binary']) for a in json.load(sys.stdin)['data']['agents']]"
env -i HOME=$HOME PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin python3 -c "import sys; sys.path.insert(0,'$HOME/herdr'); from herdr.agent_binary import resolve_agent_binary; print(resolve_agent_binary('codex'))"
```

实际修复证据：`/api/project` 返回六 Agent 全部 `ready`，binary 均为绝对路径
（codex/claude→`~/.volta/bin`，qodercli→`~/.qoder-cn/entry/qodercn`，agy→`~/.local/bin/agy`）；
最后一条命令模拟 LaunchAgent 精简 PATH，解析同样成功。

## 9. "清空"类机制的结构性失效:pane 复用从未发生,清理应做在生命周期终点而非复用入口

### 问题背景

工作流结束后每个阶段 tab 遗留 2+ pane,agent 上下文无限累积。系统里存在的
"清空设置"(dispatch 前向 pane 发送 `/clear`,`bin/herdr-task` dispatch_task)
每次派发都在执行,却从未产生过清空效果——因为它的设计前提是"复用旧 pane",
而 `herdr/pane_pool.py:_claimed_panes` 对 tasks.json 中所有带 pane_id 的任务
永久占用(不过滤状态、pane_id 永不释放),`acquire_pane_for_task` 永远找不到
可用 pane,于是每个任务都拿到全新 pane,`/clear` 每次都打在空白容器上。
同时 `herdr` 不持久化终端 scrollback(CLI 已无 `--source logfile`,旧引用失效),
pane 一关画面即失,销毁与证据天然冲突。

### 经验教训

| 问题 | 教训 | 规范 |
|------|------|------|
| /clear 每次执行却从未生效 | 清理机制挂在"复用入口"上,而复用通道被另一处代码结构性堵死——两个模块各自正确,组合起来是死代码 | 评审清理/重置类机制时,先验证它的**触发前提**在真实链路里是否成立,而不是只验证机制本身会执行 |
| 用 /clear 复用容器防上下文污染 | 清空旧容器永远清不干净(agent 私有命令、磁盘 session、scrollback 残留),这是幻觉温床 | 隔离靠"生新死灭"(新 pane + 新会话 + 用后销毁),不靠清空复用;跨任务只传固化产物 |
| pane/clone 保留被当成默认 | "保留现场"是显式例外(排障),销毁才是默认;默认保留会让上下文与磁盘单调膨胀 | 资源生命周期必须有终点:验收收敛 → 证据固化 → 销毁活体 |
| 关共享 tab 连带销毁其他 workflow 的 pane 且无证据 | 共享资源的批量销毁必须先验证归属,否则会误伤"不在本次操作范围内"的现场 | 破坏性批量操作前枚举受影响对象并校验所有权;无法枚举时放弃操作 |

### 操作规范

1. 任务收尾统一走 `herdr-task finalize <task_id>`(单任务)或
   `herdr-task close-workflow <wf>`(批量/自动);固定顺序:**转写 dump →
   pane close → 分档删 clone → 状态推进**,证据在
   `~/.herdr-controller/logs/tasks/<task_id>/`。
2. clone 删除前必须满足:有 `integration_ref`(已完成 integrate)或 `superseded`;
   `committed` 未 integrate 拒删;mode=none 任务需 `--purge-clones` 显式授权。
3. 关闭阶段 tab 前必须经 `_tab_foreign_panes` 校验归属;pane list 失败时
   宁可跳过不可盲关。
4. failed 任务现场默认保留(`--force` 才收);总指挥 pane 保留到知识沉淀
   与 PR 合并之后,用 `close-workflow --include-coordinator` 收口。
5. 禁止恢复"pane 复用 + 清空"路线:`_claimed_panes` 永久占用是隔离原则的
   执行机制;若未来引入复用,必须连同 per-agent clear 映射与 session 重启
   一起设计,并重开评审。

### 验证命令 / 证据

```bash
pytest tests/test_workflow_finalize.py tests/test_stage_advance_and_supersede.py
herdr-task close-workflow wf-nexusarchive-54433229-20260912-232500 --dry-run
herdr-task close-workflow wf-nexusarchive-54433229-20260912-232500
```

实际收尾证据:15 份 `~/.herdr-controller/logs/tasks/nx09122325-*/terminal.log` 落盘;
5 个 clone 删除(3 集成 + 2 superseded)、10 个 docs clone 按安全档保留;
wA 现场 24 pane/7 tab → 1 pane(总指挥)/1 tab;controller 自动收尾使
9/9 历史 workflow 到达 `completed`。决策全记录:
`docs/walkthroughs/20260913-workflow-finalize.md`。

---

## 10. 内嵌单行前端资源的三类暗雷：字号/圆角与间距同值、测试字符串锁、属性级正才可盲改

### 问题背景

Console 前端全部内嵌在 `console/herdr_factory_console.py` 的两个单行字符串里
（L447 CSS、L448 HTML/JS）。2026-09-13 按 snapping-ui-to-grid 技能做全量间距
白名单治理（36 处裸值）时暴露：盲替换 `14px→16px` 会误伤 `font-size:14px`；
`padding`/`gap`/`margin` 各自上下文不同值不同，同值替换会跨语义；同时
`tests/test_console_run_job.py` 等用 `assertIn` 把 JS 关键子串
（如 `state.opsMode?'← 返回工厂':'进入运维驾驶舱'`）钉死在源码上。

### 经验教训

| 教训 | 说明 |
|------|------|
| 单行 CSS 里同数值不同语义 | `14px` 同时是 padding 与 font-size；替换必须以"属性名+选择器"为锚，不能以数值为锚 |
| 测试断言是隐性 API | 源码字符串被 pytest `assertIn` 锁定的部分等价于对外契约，改前先 grep tests/ |
| 纪律门禁要可执行 | "间距只用 4/8/16/24/32"这类规范必须配直方图脚本/grep，否则必然回潮 |

### 操作规范

1. 改内嵌前端资源前先跑 `grep -n 'assertIn' tests/test_console_*.py` 列出字符串锁；
2. 数值替换一律用属性级锚定（含前后选择器片段），改完跑属性级直方图复核：
   `python3 -c` 提取 `(padding|margin|gap)(-[a-z]+)?:` 捕获组统计 px 值；
3. UI 规范类约定同步落到可执行 grep（技能自带命令或等价脚本），收尾必跑。

### 验证命令 / 证据

```bash
grep -n 'assertIn' tests/test_console_*.py
sed -n '447p' console/herdr_factory_console.py | \
  grep -E '(padding|margin|gap)[^:;}]*:[^;}]*[^0-9.](5|6|7|9|11|13|14)px'  # 期望零命中
pytest tests/test_console_run_job.py tests/test_console_templates.py \
  tests/test_console_view_state.py tests/test_console_agent_roster.py \
  tests/test_console_stage_summary.py   # 48 passed
```

决策全记录：`docs/walkthroughs/20260913-console-grid-alignment.md`。

## 11. Qoder 身份漂移第三幕：修复在 repo，病灶在外部工具层（herdr 集成装错产品目录）

### 问题背景

用户报告"agent 中的 qoder 不对，正确的是 qodercn，已经改过两次还没好"。
前两次修复（§6 commit 7d6dc5a、§8 commit 00d52ba）都在 repo 内收敛
qodercli→qodercn 的二进制映射（preflight/console 探测层），但 Qoder 系 agent
的会话上报从未成功过——`herdr agent list` 里 qodercli 条目始终没有
`agent_session`（claude/codex/opencode 都有）。全局排查发现病灶在 repo 之外：
homebrew `herdr` 工具（terminal workspace manager）的
`herdr integration install qodercli` 把 SessionStart 钩子硬编码装进**国际版
Qoder 产品目录** `~/.qoder`（其二进制 strings 仅含 `.qoder`，无 `.qoder-cn`），
而实际运行的是 Qoder CN CLI（`~/.local/bin/qoderclicn`，配置目录
`~/.qoder-cn`），读不到该钩子 → 上报链路自安装起就是断的。
本机并存两个不同产品：国际版 Qoder（`~/.qoder`，CLI 名 `qoder`）与 Qoder CN
（`~/.qoder-cn`，官方命令 `qodercn`，实际二进制 `qoderclicn-1.1.51`）；
`~/.local/bin/qodercli` 是人造 symlink 指回 CN entry。

### 经验教训

| 教训 | 说明 |
|------|------|
| 修复必须覆盖"真正执行的那一层" | repo 的 `herdr/agent_binary.py` 只管探测；拉起进程的是外部 herdr 工具内置 kind 表，会话上报靠 CLI 产品目录里的钩子。只改 repo 永远碰不到病灶 |
| 工具层别名把两个不同产品并成一个 | herdr 检测 manifest `aliases=["qoderclicn","qoder","qodercn"]` 把国际版 qoder 混为同一 agent；安装器硬编码 `~/.qoder`。产品级区分必须在工具层显式纠正（本地 manifest 覆盖） |
| CLI 钩子有"目录信任"门禁 | QoderCN CLI 对钩子报 `Security: Blocked execution of hook (user) in untrusted folder`：cwd 不在 `permissions.trustDirectories`（默认 `["/Users/user"]`）内则 SessionStart 钩子一律不执行。在 /tmp 里验证必然假阴性；工厂克隆目录天然受信任 |

### 操作规范

1. 排查 agent 身份类问题按五层取证：repo 映射（`herdr/agent_binary.py`）→
   运行时状态（`~/.herdr-controller/*.json`）→ 拉起层（herdr 工具 kind/检测
   manifest）→ CLI 产品配置（`~/.qoder` vs `~/.qoder-cn`）→ 实际进程
   （`ps aux` + `ps eww` 看配置目录 env）。
2. QoderCN 的 herdr 集成以 `~/.qoder-cn` 为准；任何人再跑
   `herdr integration install qodercli` 会装回 `~/.qoder`，必须重做迁移
   （步骤见 walkthrough）。
3. 验证钩子必须在受信任目录内起真实 agent（`--cwd ~/herdr` 或
   `~/.herdr-controller/clones/*`），以
   `herdr agent list` 中 `agent_session.source=="herdr:qodercli"` 为准。
4. 检测别名收敛用本地覆盖 `~/.config/herdr/agent-detection/qodercli.toml`
   （local 永远 shadow remote；remote manifest 更新后需人工同步别名修正）。

### 验证命令 / 证据

```bash
herdr server agent-manifests   # qodercli: source_kind="local override", local_override_shadowing_remote=true
herdr agent explain wA:p2A     # manifest: /Users/user/.config/herdr/agent-detection/qodercli.toml
herdr agent list               # 修复后 qodercli 首次出现 agent_session（source=herdr:qodercli）
grep "herdr-agent-state" ~/.qoder-cn/logs/runs/<run>/qodercli.log  # hook.started 记录
```

决策全记录：`docs/walkthroughs/20260913-qodercn-agent-identity-fix.md`。


## 12. 流程完成 ≠ 交付完成:质量门的"不通过"必须驱动结构回流,而非归档

### 问题背景

wf-nexusarchive-…-084418 全流程走完:评审产出 B1(P0)阻断结论,但结论只存在
于自然语言报告——引擎 `is_node_complete` 只看任务状态,照常推进 wrapup 并将
交付被阻断(PR 禁合)的 workflow 归档 `completed`。修复只能在新的孤立
workflow 里另起炉灶,丢失 PR 关联与阶段历史。审查轮 1(对抗性)进一步发现
初版设计三处结构漏洞:verdict 死循环(作废范围漏 gate 自身)、重测缺失
(下游闭包不完整)、reopen 自消除(sweep 会把重开的 workflow 秒回 closed)。

### 经验教训

| 教训 | 说明 |
|------|------|
| 完成态判定与结论语义是两层 | 任务"完成"只证明交付物存在;pass/blocked 是另一维状态。质量门的结论必须有机器可读载体并被推进逻辑消费,否则最强质量信号被浪费 |
| 回流的正确粒度是"retry_node 全部下游" | 只回炉 gate 自身会死循环(旧 verdict 残留),只回炉 gate 不回炉下游会跳过重测。作废闭包必须覆盖 gate+全部下游 |
| 结构性消除竞态优于防线叠加 | "节点未完成"本身就是闩(作废后周期 sweep 打不穿),不需要额外锁位;给 gate 打 notified 反而会被 reconcile 的前置依赖判定卡成永久停摆 |
| reopen 类"复活"操作自带自消除竞态 | 旧状态仍满足终态判定时,下一个周期事件就会把它再次终结。需要显式闩 + 明确的摘除时机(首个活跃任务) |
| 独立审查要给对抗性清单,并复核其建议 | 审查抓到 3 个设计级漏洞,但也给出 1 个会造成永久停摆的修法(用回归测试证伪后拒绝) |

### 操作规范

1. 门禁阶段验收必须落 verdict:`herdr-task set <t> completed --verdict
   pass|blocked --note`(blocked 必填 note);
2. blocked 的恢复路径:Controller 自动作废 gate+下游 → 总指挥按 fix_loop
   事件派发 fix task(`--onto` 落 PR 分支)→ DAG 自动重流;禁止新建
   workflow、禁止放弃;
3. 放弃交付必须显式:`close-workflow --abandon`(outcome=abandoned),
   console 的候选分支合并/手工推进遇 blocked verdict 一律拒绝;
4. 复用已关闭 workflow:`reopen-workflow`,首个任务派发前闩保护。

### 验证命令 / 证据

- `/opt/homebrew/bin/pytest tests/`(183 passed,含 fix-loop 33 用例);
- 设计与审查记录:`docs/walkthroughs/20260913-fix-loop-design.md`(§8 审查修订);
- 知识同步:`wiki/task-lifecycle.md` §1.1、`wiki/dag-workflow-engine.md` §10。
