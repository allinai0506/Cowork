# Workflow 物理收尾机制(finalize / close-workflow)设计与交付记录

> 日期:2026-09-13
> 关联:wf-nexusarchive-54433229-20260912-232500 复盘 → 设计思维链 → 实现落地
> 状态:已实现、已测试(134 passed)、已在真实工作流上完成端到端验证

---

## 1. 任务目标与背景

**起点问题**:工作流结束后,每个阶段 tab 下遗留 2+ 个 pane,agent 上下文永不销毁。

**复盘发现的两层事实**:

1. pane 膨胀不是 bug:每阶段 tab = 1 锚点 + 历史累计任务 pane;任务 cleaned 后
   `pane_persistent: true` 保留现场是当时设计;
2. 用户记忆中的"清空设置"= dispatch 前发 `/clear`(`bin/herdr-task` dispatch_task),
   但它结构性失效:`_claimed_panes()` 不过滤任务状态、pane_id 永不释放 →
   pane 复用从未发生过 → `/clear` 每次都打在全新 pane 上(空转)。

**用户确立的设计原则**(推翻了最初的"释放 pane 回池子复用"方向):

> 任务应独立执行、隔离,避免上下文污染与上下文过大导致幻觉。

由此推导出生命周期公理:**生而隔离,死而清零;上下文只在任务体内生存,
跨任务唯一合法通道是固化产物(git / 文档 / 任务记录)。销毁是默认,保留是例外。**

## 2. 改动范围

| 文件 | 改动 |
|------|------|
| `bin/herdr-task` | 新增 `finalize` / `close-workflow` 子命令;新增 `_herdr`(绝对路径解析)/ `delete_clone_safely` / `clone_deletable` / `dump_transcript` / `_finalize_one` / `_tab_foreign_panes` / `_workflow_stage_tabs`;purge 门槛放宽到非 ACTIVE;`stage_reset` 改用 `STAGE_STATE_FILE` 常量 |
| `services/herdr-controller.py` | `check_workflow_stage_advance` 的 `[WORKFLOW COMPLETE]` 分支挂 `maybe_close_completed_workflow`(后台线程 + in-flight 防重入 + status=completed 短路) |
| `tests/test_workflow_finalize.py` | 新增 22 个用例(闸门/证据/clone 安全/幂等/dry-run/共享 tab 守卫) |
| `tests/test_stage_advance_and_supersede.py` | stage_reset 测试改 patch `STAGE_STATE_FILE`(原 patch expanduser 因常量化失效) |
| `docs/references/cli-reference.md` | 新增 2.6/2.7 命令文档 |

## 3. 设计决策与权衡(规格未写明、实现时拍板的)

### 3.1 撤回"pane 复用 + /clear"方案(方向反转)

初版方案 A 是让 cleaned 任务把 pane 交还池子,靠 dispatch 前 `/clear` 清上下文。
推演后否决:复用旧容器恰恰引入污染渠道(/clear 各 agent 语义不一、agent 磁盘
session 清不干净、scrollback 残留)。**隔离必须靠"生新死灭",不靠"清空旧容器"。**
`_claimed_panes` 的"永久占用"因此保留原样——它现在是隔离原则的执行者而非 bug。

### 3.2 clone 删除分安全档位(比口头方案更保守)

口头方案说"验收后删 clone",但数据核实发现:**mode=none 的 docs/test/review 任务
(本 workflow 12/15)没有任何 integration 通道,其成果只存在于 clone 与终端转写**。
盲删会丢数据。最终规则:

- `integration_ref/branch` 存在(已完成 git integrate)或 `status=superseded`(从未
  交付,替代任务负责成果)→ 删;
- 其余 → 保留,报告标注原因,`--purge-clones` 显式授权后才删。
- committed 未 integrated → 拒删并提示先 integrate(commit 还只在 clone 里)。

### 3.3 证据先行:转写 dump 是销毁的前置步骤

验证发现 herdr 不持久化终端 scrollback,且 CLI 已无 `--source logfile`
(`bin/herdr-task` 旧引用已失效)。pane 一关画面即失。故 finalize 固定顺序:
**dump(`pane read --source recent-unwrapped --lines 20000`)→ close → 删 clone**,
dump 到 `~/.herdr-controller/logs/tasks/<task_id>/terminal.log` + `meta.json`
(含 agent_session id)。dump 失败降级 warning,不阻塞收尾。

### 3.4 共享 tab 连带销毁守卫(上线当天抓到的真实 bug)

连续多个 workflow 复用同一 workspace 的阶段 tab。首个真实自动收尾
(xiyu-155001)关 tab 时,连带销毁了旧 workflow(xiyu-230101)仍驻留的 pane,
且未 dump。修复:`_tab_foreign_panes` 在关 tab 前枚举 tab 内存活 pane,
凡有不属于本 workflow(锚点 + 本 workflow 任务 pane 之外)的外来 pane →
跳过该 tab 并写入报告 `tabs_skipped`;pane list 失败或 workspace 未知 →
同样放弃关 tab(宁可保留,不做盲目销毁)。已发生的 230101 转写损失如实记录,
其 clone/分支/任务记录完好。

### 3.5 状态机三处适配

- purge 门槛从 `cleaned/failed` 放宽到一切非 ACTIVE(修 superseded 终态死锁:
  superseded 之前永远无法 purge);
- finalize 对 completed/integrated/cleanup_ready 按 `FINALIZE_ADVANCE` 链自动
  推进到 cleaned(completed→cleanup_ready 为合法转移);committed 不动(需先 integrate);
- failed 任务在 close-workflow 中默认**保留现场**(排障价值),报告列
  `retained-failed`,finalize 单点操作需 `--force`。

### 3.6 总指挥 pane 慢半拍

自动收尾关任务 pane + 阶段 tab,总指挥 pane 保留——它是全 workflow 上下文最大
的活体,但知识沉淀(AGENTS.md 收尾 SOP)发生在 wrapup 之后。报告尾部输出提醒:
沉淀 + 合 PR 后运行 `close-workflow <wf> --include-coordinator`。

### 3.7 零任务中止运行的语义

当晚 3 个中止运行(192353/192922/193720)零任务,`is_node_complete` 对空任务集
永远 False → 自动触发不命中(也不会重试刷屏)。close-workflow 对"零任务但
workflows.json 有登记"的 workflow 视为平凡完成:直接标记 completed + 清
stage-state;无登记的 id 才报错(防手滑)。

### 3.8 /clear 降级为防御动作(P4,本次未实施)

dispatch 前 /clear 在"生新死灭"模型下失去主战场。后续如实施,应改为仅对
已有 agent 会话的 pane 发送,并做 per-agent 命令映射。本次未动该代码。

## 4. 验证与测试数据

```bash
pytest tests/  # 134 passed, 12 subtests passed
herdr-task close-workflow wf-nexusarchive-54433229-20260912-232500 --dry-run
herdr-task close-workflow wf-nexusarchive-54433229-20260912-232500
launchctl kickstart -k gui/$(id -u)/com.user.herdr-controller
```

真实收尾结果(2026-09-13):

- wf-…-232500:15 任务全部 finalized,review-recheck completed→cleaned 自动推进;
  15 份 terminal.log + meta.json 落盘;5 个 clone 删除(3 git 集成 + 2 superseded),
  10 个 docs clone 按安全规则保留;wA 从 24 pane / 7 tab 收敛到 1 pane(总指挥)/ 1 tab;
- controller 重启后自动机制即刻生效:xiyu-155001、nexusarchive-194638 等历史完成
  workflow 被自动收尾,最终 9/9 workflow 全部 `completed`;
- 上线当天抓到并修复共享 tab 连带销毁 bug(§3.4),守卫有 2 个专测钉住。

## 5. 遗留与后续(P4 候选)

- `/clear` per-agent 映射与降级;
- 把"验收不依赖 pane 读"从 e2e-only 推广为全 workflow 默认(隔离销毁的逻辑闭环);
- agent 磁盘 session 文件(OpenCode ses_*)的清理(量级小,已记录 id 可追溯);
- Console 任务卡展示"已销毁 + 证据路径"形态。

## 6. 观察期决定(2026-09-13 复盘追加)

**问题**:close-workflow 默认连阶段 tab 一起关(仅留总指挥),用户倾向只清 pane。
**思维链结论**:tab 是可逆资源(自愈按 label 重建 / 下一 workflow 按 label 认领),
pane 上下文不可逆;删 tab 的成本是风险级(连带销毁类),留 tab 的成本是观感级
(空 tab、孤儿 tab 仅在 stage 改名时出现)。不确定性下选可逆性高的方向。

**决定:暂不返工,保留现状(删 tab 默认)进入观察期**。理由:安全网已全部就位
(归属守卫、dry-run 同构、证据先行、逐项报告),切换方向是 flag 级改动(~5 行 + 2 测试),观察结论出来再动手不亏。

**观察协议**(载体 = 下一个真实 workflow 的完整生命周期,2-3 个为样本量):

| 信号 | 含义 | 指向 |
|------|------|------|
| 收尾报告出现 `tabs_skipped` foreign pane | 守卫拦截了跨 workflow 干扰,删 tab 有真实风险 | 切 pane-only 默认(tab 关闭改 `--close-tabs` 显式) |
| TOPOLOGY HEALED 重建造成实际困扰(首派发延迟/label 漂移/孤儿 tab) | 删 tab 的恢复成本真实存在 | 切 pane-only 默认 |
| 真实出现"想回已完成 stage tab 回看"的需求 | 保留现场有真实价值 | 切 pane-only 默认 |
| 2-3 个 workflow 无任何摩擦 | 删 tab 无隐性成本 | 维持现状 |

**已知接受的边角**:新 workflow 认领旧 tab 后、若其任务 pane 尚未生成,旧 workflow
迟到的 close-workflow 可能把该 tab 连新锚点一起关闭(守卫放行"只有锚点"的 tab);
后果是自愈重建(噪音非数据损失),且正是观察信号 1 要捕捉的形态。

**顺手项(观察期外)**:遗留陈旧登记 test-register-001 / e2e-impl-002
(pane 已不存在却停在 pending/dispatched)可用 supersede 清理,消除 ops-center 异常误报。
