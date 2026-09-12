# Herdr 多 Agent 自动化编程系统 — Handoff

## 1. 当前结论

这套系统已经从“Herdr 终端工作区”扩展成了一个可运行的多项目、多阶段、多 Agent 自动研发流水线。

当前核心目标已经跑通：

- 多项目自动识别
- 每个项目独立 Herdr Workspace
- 固定阶段 Tab
- Persistent Pane 保留
- Agent Router
- Agent 负载均衡 / reservation
- 动态 Agent 分配
- CoW Clone 隔离
- Task Registry
- Controller 自动推进
- macOS 系统通知
- Workflow Candidate Branch
- Test / Review 基于 Candidate，不直接污染 main
- Task Pane / Clone 默认保留
- 手工 purge 才物理清理

---

## 2. 当前真实项目

### 项目 A：nexusarchive

- project_root: `/Users/user/nexusarchive`
- workspace: `w6`
- coordinator: `w6:p1H`
- base branch: `dev`

### 项目 B：xiyu-bid-poc

- project_root: `/Users/user/xiyu/xiyu-bid-poc`
- project_id: `xiyu-bid-poc-a380753e`
- workspace: `w9`
- coordinator: `w9:p1`
- base branch: `main`
- workflow config:
  `~/.herdr-controller/projects/xiyu-bid-poc-a380753e/workflow.json`

当前真实 Workflow：

`wf-xiyu-bid-poc-a380753e-20260911-230101`

当前阶段状态（最近一次）：

- requirements: cleaned
- plan: cleaned
- implementation: cleaned
- test: cleaned
- review: working
- wrapup: pending

review Task：

`review-01-impl-quality-gate`

---

## 3. Herdr 结构模型

当前约定：

- Workspace = 项目
- Tab = 研发阶段
- Pane = AI 工位 / Task 现场
- Agent = AI 员工
- Task = 工作单
- Coordinator = 项目经理
- Controller = 调度器

固定阶段：

1. 总指挥
2. 需求分析
3. 计划
4. 实现
5. 测试
6. 评审
7. 收尾

Tab 永久保留。

Task Pane 默认保留。

Clone 默认保留。

只有显式：

`~/herdr-task.py purge <task-id>`

才物理清理 Pane + Clone。

---

## 4. 日常入口

在普通 macOS Terminal 中：

```bash
cd /某个/git/项目
~/herdr-factory run "自然语言需求"
```

系统会：

1. 自动识别 Git 项目
2. 找到或创建该项目 Herdr Workspace
3. 注册 Workflow
4. 发送给项目 Coordinator
5. requirements → plan → implementation → test → review → wrapup
6. 需要人工处理时由 macOS 通知提醒

查看状态：

```bash
~/herdr-factory status <workflow-id>
```

查看项目：

```bash
~/herdr-factory projects
```

查看当前项目 Agent Pool：

```bash
~/herdr-factory agents
```

查看 Persistent Pane：

```bash
~/herdr-factory slots
```

绑定 Pane：

```bash
~/herdr-factory slot-bind <pane-id> codex
```

---

## 5. Agent 指定与自动路由

当前两种模式都支持。

### 自动

```bash
~/herdr-factory run "需求"
```

等价于：

`--agent auto`

Router 根据：

- stage
- task_type
- 当前 Agent 负载
- Agent Pool
- disabled_agents
- reservation

动态选择 Agent。

### 整个 Workflow 强制指定

```bash
~/herdr-factory run --agent codex "需求"
```

### 单 Task 强制指定

`~/herdr-task.py launch ... --agent codex`

### 当前 Agent Pool 默认候选

- opencode
- codex
- qodercli
- claude
- agy
- pi

重要：

Router 当前无法自动知道“某个 Agent token 已耗尽”。

如果某 Agent 暂时不可用，应：

- 手工指定另一个 Agent
- 或把该 Agent 放入项目 `disabled_agents`

后续建议新增：

`herdr-factory preflight`

自动检查：

- CLI 是否存在
- 登录状态
- API Key / token 是否可用
- 首次启动弹窗
- workspace trust
- 更新提示
- Agent 是否 ready

---

## 6. Agent Router 的关键工程点

最初 Router 只是按优先级选择第一个 Agent。

这导致：

- 多 Task 仍然都落到 OpenCode
- 看似多 Agent，实际是单 Agent

后来加入负载均衡。

但并发 launch 时仍有竞态：

Task A 和 Task B 同时看到：

- opencode load=0
- codex load=0

于是都选 OpenCode。

最终加入 atomic reservation：

- 文件锁
- `agent-reservations.json`
- task_id reservation
- Task 注册后释放 reservation

因此多个并行 Task 才能真正分散到不同 Agent。

---

## 7. Persistent Pane

支持两类 Pane：

### 用户预建 Pane

你可以在 Herdr 的某个 Stage Tab 里先手工创建空 Pane。

Factory 会自动发现这些 Pane。

可以绑定：

```bash
~/herdr-factory slot-bind w9:pC codex
```

### 动态 Pane

没有合适 Persistent Pane 时才动态创建。

规则：

- 有历史 Task 的 Pane 不复用
- 已有 Agent 的 Pane 不抢占
- Anchor Pane 不占用
- Task 完成后 Pane 保留

这保证了“AI 员工工作现场”可观察、可追溯。

---

## 8. CoW Clone

每个 Task 使用完整仓库 Clone：

`~/.herdr-controller/clones/<task-id>`

macOS APFS：

`cp -cR`

特点：

- 快
- 全仓库上下文
- Task 隔离
- 不污染主工作区

每个 Task：

- 独立 branch
- 独立 Clone
- 独立 Pane
- 独立 Agent

---

## 9. Baseline Fingerprint

不能直接用 `git status` 判断 Agent 改了什么。

原因：

Clone 会继承 Task 创建前主仓库里已有的：

- tracked 修改
- untracked 文件

因此 Worker 在 Task 创建时记录：

`baseline_fingerprint`

验收时：

```bash
~/herdr-task.py verify-baseline <task-id>
```

结果：

- `BASELINE_MATCH`
- `TASK_CHANGED`

这是判断 Task 真正变化的唯一事实来源。

---

## 10. Git 集成模型

不能直接把 Agent 分支 merge 到 main/dev。

当前正确模型：

Task Branch
→ Integration Branch
→ Workflow Candidate Branch
→ Test
→ Review
→ Wrapup
→ 最后才决定是否进入 main

真实 Workflow Candidate：

`herdr/workflow-wf-xiyu-bid-poc-a380753e-20260911-230101`

原因：

implementation 的多个 Task 必须先汇总，test/review 才能看到“完整候选版本”。

main 必须保持不被自动污染。

---

## 11. 主仓库 clean 判断踩过的坑

最初 integrate 要求：

`git status --porcelain` 绝对 clean。

但主仓库存在合法 untracked 文件：

- docs/artifacts/ai-capability-upgrade-2026-09.html
- .pptx
- preview.png
- make-ai-ppt.py

导致 integrate 被错误拦截。

修复后：

- tracked change → 禁止 integrate
- baseline 已存在的 untracked → 允许
- 新的 unexpected untracked → 禁止

---

## 12. complexity gate 踩坑

最初 Worker 假设所有项目都有：

`scripts/complexity-gate.cjs`

这是 NexusArchive 专用工程能力。

xiyu-bid-poc 没有该脚本，导致 Task 无法启动。

最终原则：

complexity gate 是“项目可选能力”，不是 Herdr 基础设施必选依赖。

不存在时：

`complexity_baseline=disabled`

---

## 13. Agent 首次启动问题

多 Agent 真正开始工作后，暴露出不同 CLI 的首启门。

### Codex

出现：

`Update available`

会阻塞 Agent ready。

修复：

`~/.codex/config.toml`

```toml
check_for_update_on_startup = false
```

### Claude

出现：

`Is this a project you created or one you trust?`

Task Clone 每次路径不同，因此会触发 workspace trust。

当前 Worker 已增加：

- clone 路径 trust preflight
- `~/.claude.json`
- `hasTrustDialogAccepted = true`

### Agent name collision

失败 launch 后 Agent session 可能残留。

再次用相同 task_id 启动：

`agent_name_taken`

修复：

Agent name 使用：

`task-id + pane hash`

不再直接等于 task_id。

---

## 14. Prompt / Agent lifecycle 踩坑

Herdr `agent prompt` 存在实际 Prompt 已送达但：

- lifecycle 没及时变化
- stalled
- 文本停在 composer
- alternate-screen 无法读完整历史

因此增加：

- prompt marker
- send-text fallback
- Enter fallback
- Sentinel
- done / idle fallback
- crash detection

Agent 状态不能作为唯一事实来源。

---

## 15. Sentinel

后台：

`~/herdr-sentinel.py`

用于兜底：

- Prompt 卡 composer
- lifecycle 漏事件
- `HERDR_TASK_DONE:<task-id>`
- Bun crash
- segmentation fault

但不要把 Sentinel 当业务 Controller。

Sentinel 是异常兜底层。

---

## 16. Controller

核心职责：

- 监听 Task Agent 状态
- dispatched → working
- working → blocked
- blocked → working
- working → done/idle → agent_done
- 通知 Coordinator 验收
- completed → commit/integrate/cleanup
- Stage Advance

已出现过的重要坑：

### completed 后 Controller 重启

Coordinator 已 set completed，但 finalize 没执行。

Task 会永久卡住。

尝试做全局 recovery 时又误扫历史 completed Task，导致 Controller 被旧垃圾 Task 卡死。

最终正确方向：

只对：

- 当前活跃 Workflow
- finalize_pending=true

做状态恢复。

这一块仍建议后续继续完善。

---

## 17. macOS Notifier

后台服务：

`~/herdr-notifier.py`

launchd：

`com.user.herdr-notifier`

通知：

- BLOCKED
- FAILED
- HUMAN REVIEW / NEEDS ACTION
- Workflow Complete

目标：

用户不需要一直盯着 Herdr。

---

## 18. 为什么最初 E2E 很顺

E2E 环境非常简单：

- 单项目
- 单 Workspace
- 多数任务 OpenCode
- integration_mode=none
- 不改真实业务代码
- 不涉及多个 implementation Task 汇总
- 不涉及 Candidate Branch
- 不涉及真实 Git integration
- 不涉及 Claude/Codex 首启
- 不涉及 token 不足
- 不涉及多项目路由
- 不涉及 Persistent Pane
- 不涉及真实验收依赖

所以它验证的是：

“流水线骨架能跑”

而真实项目验证的是：

“软件工厂能不能工作”

两者复杂度完全不同。

---

## 19. 三天踩坑后的核心工程原则

### 原则 1：控制面和执行面分离

Controller 负责调度。

Agent 负责执行。

不要让 Agent 自己决定基础设施状态。

### 原则 2：Agent 是可替换资源

不要把流程绑定到某一个 Agent。

必须有：

- Router
- Pool
- disabled
- override
- fallback

### 原则 3：Task 必须隔离

每个 Task：

- Clone
- Branch
- Pane
- Agent

### 原则 4：Pane/Tab 是工作现场

默认保留。

不要 Task 一结束就删除。

### 原则 5：真实项目必须有 Candidate

多个实现成果必须先汇总到 Workflow Candidate，再进入测试和评审。

### 原则 6：所有“首次启动交互”必须 preflight

否则多 Agent 自动化一定会随机卡住。

### 原则 7：状态机必须可恢复

任何一步：

completed
committed
integrated
cleanup_ready

都必须能在 Controller 重启后继续。

不能依赖“一次事件绝不丢失”。

### 原则 8：历史 Task 不能污染当前 Workflow

所有判断必须按：

workflow_id + project_id

隔离。

### 原则 9：通知必须系统级

用户只在真正需要人工介入时收到 macOS 通知。

### 原则 10：自动化不能以破坏安全边界为代价

main/dev 不能直接被自动 Agent 污染。

---

## 20. 下一轮建议优先优化

按优先级：

### P0

1. finalize_pending 精准恢复机制
2. Workflow Candidate 自动生成
3. Candidate 自动接收所有 implementation Integration Branch
4. test/review/wrapup 自动继承 Candidate Branch
5. Workflow 最终 merge / PR 门禁

### P1

6. `herdr-factory preflight`
7. Agent token / auth health
8. 自动 disabled_agents
9. Agent 首启统一治理
10. failed launch orphan 自动清理

### P2

11. `herdr-factory watch`
12. macOS 通知点击后定位对应 Pane
13. Agent Router 能力评分
14. Agent 历史成功率
15. 成本 / token / latency 路由
16. 并行 Task 依赖图
17. Workflow dashboard

---

## 21. 新聊天建议开场

把本文件上传给新的 ChatGPT 会话，然后直接说：

> 这是我们前一个会话搭建 Herdr 多 Agent 自动软件工厂的 handoff。请完整读取后继续，不要重新设计已有架构，也不要让我重复已经跑通的步骤。当前优先继续完善 P0：finalize_pending 精准恢复 + Workflow Candidate 自动化。

