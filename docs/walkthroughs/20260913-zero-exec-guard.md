# 零执行守卫与失败原因传导(zero-exec guard / failed --reason)设计与交付记录

> 日期:2026-09-13
> 关联:wf-nexusarchive-54433229-20260913-084418 wrapup 假失败停滞事故复盘
> 状态:已实现、已测试(151 passed)、controller 已热重启生效

---

## 1. 事故复盘(本次修复的成因链)

wf-…-084418 的收尾任务 `wrapup-01-mainchain-summary`(qodercli)派发后,
前任会话已因两次 `/quit` 正常退出,新起的 qodercn 进程停在空提示符,
**派发 Prompt 以 Queued 形态残留、从未被提交执行**。由此引发三层连锁:

1. **假 done**:Agent 0.4 秒后 idle,事件流被判定为 `working → agent_done`
   (实际零执行);
2. **假 failed**:Controller 走正常 done 验收流通知总指挥,总指挥
   `verify-baseline` 无变化,如实落盘 `failed`(且当时无 `--reason`,
   通知只有"需要人工查看");
3. **终态静默**:`failed` 为终态,controller/sentinel 均不再过问,
   macOS 通知无决策指引 → 工作流停滞,等待人工排查。

人工介入后经总指挥排查根因、原 Pane 恢复 Agent、`launch --supersedes`
派发新收尾任务(`failed → superseded` 合法转移)完成闭环——但这套恢复
完全是人工驱动的,本修复将其中的"假 done 识别"与"失败上下文传导"自动化。

## 2. 改动范围

| 文件 | 改动 |
| --- | --- |
| `services/herdr-controller.py` | ① 零执行守卫:`ZERO_EXEC_MIN_SECONDS`(默认 5s,env `HERDR_ZERO_EXEC_MIN_SECONDS`)+ `working_elapsed_seconds` / `route_zero_exec_suspect` / `complete_working_task`;`handle_event` idle/done 与 `reconcile_task_state` RECOVERY 四处收口全部过守卫;`build_coordinator_message` 新增 `zero_exec` 模板。② done 验收模板与 RETRY 模板要求 failed 必须带 `--reason` |
| `bin/herdr-task` | `set` 子命令新增 `--reason`;`set_status` 在落盘 `failed` 时持久化 `failure_reason` |
| `tests/test_zero_exec_guard.py` | 新增 11 个用例 |
| `wiki/task-lifecycle.md` / `wiki/log.md` | §1.1 零执行守卫 + log 同步 |

## 3. 规格之外的决策与权衡

1. **守卫触发后的落点选 `blocked` 而非 `rework`/保持 `agent_done`**:
   `blocked → working` 是合法转移,总指挥向原 Pane 重送 Prompt 后 Agent
   真实执行,状态机自动回流;且 `blocked` 属于 ACTIVE,不会被
   close-workflow 误收尾。语义上"任务从未开始"更接近"等待处理"而非"返工"。
2. **重派由总指挥执行而非 Controller 自动重发**:tasks.json 不存派发
   Prompt,Controller 无从重发;且死会话场景下(本次事故)机械重发
   会再次落入 Queued 残留。总指挥具备读 Pane 判断死活的能力,由它
   查证后重送更稳。`zero_exec` 模板明确禁止其落盘 completed/rework/failed。
3. **阈值 5 秒的取值依据**:本 workflow 实测数据——两次假 done 分别为
   0.38s / 0.09s,最快的真实执行(plan-01 首轮)为 12.5s;5s 居中留有余量。
   已知最短真实执行与阈值间仍有 2.5 倍余量,误伤时代价可控
   (blocked → 总指挥查证 → 重送 Prompt,无数据损失)。
4. **`--reason` 只对 `failed` 落盘 `failure_reason`**:`herdr-notifier` 的
   `reason()` 已优先读取 `failure_reason`/`blocked_reason` 等字段,最小
   改动即可让通知正文从"需要人工查看"变为可行动的失败说明;不新增
   通知通道(controller 侧不重复调 notifier,避免与 5s 轮询双报)。
5. **修正了修复前的两个误判**:notifier 守护进程本来就在监听 failed 并
   弹 macOS 通知(①方案中"controller 调 notifier"取消);console 节点
   卡片已有 failed 渲染(②方案中"console 待人工决策状态"取消)。
   真正缺口是通知内容无决策指引 → 由 `--reason` 传导补齐。
6. **不新增 `failed → rework` 转移**:状态机已有 `failed → superseded`
   + `launch --supersedes` 的合法替代路径(本次事故的实际解法),
   复活死任务违背"生而隔离,死而清零"生命周期公理。

## 4. 验证

- `tests/test_zero_exec_guard.py`:11 passed(守卫双路径、RECOVERY 双分支、
  阈值可调、elapsed 兜底链、zero_exec 模板关键指令、--reason 三态落盘);
- 全量 `pytest`:151 passed,零回归;
- 语法检查:`ast.parse` 通过;
- 部署:`launchctl kickstart -k gui/$(id -u)/com.user.herdr-controller` 优雅重启。

## 5. 遗留与后续

- 死会话派发的**源头检测**(dispatch 前校验 Agent 会话存活、Queued 残留
  主动清理)未做——零执行守卫已兜底,源头治理待独立设计;
- `plan-01` 式 12.5s 快速真实执行与阈值余量需在生产中持续观察,
  误伤模式是 blocked 后总指挥人工确认,可接受。
