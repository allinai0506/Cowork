# Fix-Loop 机制设计:结论驱动的阶段门禁与交付终态(全盘方案·v2 修订版)

> 日期:2026-09-13
> 背景:wf-nexusarchive-…-084418 复盘——评审 B1(P0)阻断交付,引擎却以"流程走完"将 workflow 归档 completed;零执行守卫(zero-exec guard,已上线)解决的是"假 done",本设计解决的是更深一层:**质量门的"不通过"信号没有回路消费**。
> 状态:v2 已实现并合入分支 feat/fix-loop-onto-reopen(PR-1:61d0d63)与
> feat/fix-loop-gates(PR-2:08949e6 + 审查修复 d9a22cb);独立审查两轮,
> 全量 183 tests passed。实现与设计的偏差:①门禁默认值以代码层
> `GATE_DEFAULTS` 取代"内置模板配 gate"(模板无 stage_policies,行为等价);
> ②console create_candidate 一律拒绝 blocked verdict(未提供 force 参数,
> 逃生口=作废过期结论),比设计更严格;③审查轮 1 建议"作废后给 gate 打
> notified"被否决——reconcile 不会吊销依赖已恢复的 notified,会造成重流
> 永久停摆,改用两个回归测试证明时序(test_reflow_*);④审查 agent 曾越权
> 提交 factory 创建守卫与 projects.py helper,已剔除出本任务分支
> (备份:backup/agent-unsolicited),未纳入本次交付。
> v1→v2 修订记录见 §8(4 处漏洞 + 2 处绕弯)

---

## 0. 问题定义

引擎只有"任务完成"语义,没有"阶段结论"语义。`is_node_complete` 只看任务状态,`is_workflow_completed` 与 `close-workflow` 只看流程结构,均不读交付结果。后果:

- test/review 产出"不通过"报告 → 节点照常完成 → 照常推 wrapup → workflow 照常关闭;
- 交付被阻断(P0 未修、PR 禁合)的 workflow 与交付成功的 workflow 在 registry 里同为 `completed`,无法区分;
- 修复只能在**新** workflow 里另起炉灶,丢失 PR 关联、阶段历史与教训连续性。

## 1. 设计目标与原则

| # | 原则 | 含义 |
| --- | --- | --- |
| G1 | 结论驱动 | 门禁阶段(test/review/wrapup)的 pass/blocked 结论是 DAG 推进的一等输入 |
| G2 | 原地修复 | blocked → controller 作废受影响任务、向目标节点回炉,复用既有回归机制,DAG 自动按序重流 |
| G3 | 交付终态 | workflow 的完成/收尾以 wrapup verdict 为准;blocked 不可自动 close,归档区分 delivered/abandoned |
| G4 | 人在环路 | 引擎负责机械回路(作废/锁位/重推);修什么、是否放弃由总指挥+用户决策 |
| G5 | 向后兼容 | 无 verdict 的既有 workflow 行为完全不变(lenient);严格模式 opt-in |
| G6 | 最小机制 | 不新增状态机状态、不加守护进程;全部复用 TRANSITIONS / registry / stage-state / 既有事件通道 |

**已验证可复用的既有机制**(fix-loop 的地基,无需新造):

- `is_node_complete` 每次从注册表现算(controller.py:338)→ 任务被作废或新任务进入即令节点回归未完成;
- `reconcile_stage_advance_states` 吊销下游 `notified` 锁;`normalize_workflow` 为线性 stages 合成 `depends_on`(workflow.py:221);
- `ensure_node_runtime` 毫秒级重建被拆除的 Tab/Anchor → close 后重派不需要恢复现场;
- `finalize` 命令幂等推进 completed→cleanup_ready→cleaned;`cleaned→superseded` 合法转移;
- worker 装配 CLI 本来就吃 `--base-branch` 必填参数,只差 launch 层语义扩展;
- **stage 检查存在周期性 sweep**(`check_all_workflows_stage_advance`)——门禁逻辑会被反复评估,一切"只拦一次"的机制都会被打穿,这是 v2 修订的关键约束。

## 2. 组件设计

### C1 verdict 结构化(原 P1)

**数据**(`~/.herdr-controller/tasks.json` 任务字段):

- `stage_verdict`: `"pass" | "blocked"`
- `stage_verdict_note`: blocker 清单与修复指引(blocked 时必填,作为 fix task 的输入)

**命令**(`bin/herdr-task set` 扩展,与既有 `--reason` 同模式):

```
herdr-task set <task_id> completed --verdict pass|blocked --note "..."
```

- 校验:`--verdict` 仅在目标状态为 `completed` 时合法;`blocked` 必须带 `--note`;
- 写入者:总指挥在验收时落盘——controller 的 done 验收模板与 RETRY 模板**强制**要求门禁阶段验收必须带 verdict(lenient 模式下忘写只告警,但模板指令必须存在,否则门禁形同虚设);
- **哪些阶段是门禁**:stage-policies.json 每阶段可选 `gate` 配置:

```yaml
test:
  gate:
    retry_node: implementation   # blocked 时的回炉目标节点
    max_loops: 3                 # 超限升级到人(默认 3)
```

缺省 `gate` 缺失 = 非门禁(lenient,向后兼容);内置模板默认给 test/review/wrapup 配 gate;wrapup 的 `retry_node` 可自指(总结没写好只需重写总结,不必回炉实现)。

**读取**:`gate_verdict(workflow_id, node_id) -> "pass"|"blocked"|None`,fail-safe 语义——**节点内存在任何未 superseded 的 completed 任务带 `stage_verdict=blocked` ⇒ blocked**;否则存在 pass ⇒ pass;否则 None(legacy 放行)。多任务节点上不做"取最新"歧义;受控流程中过期 blocked 任务由 C2 的原子作废保证不残留,手动场景的逃生口 = 对过期任务执行 supersede。

### C2 fix-loop 回路(原 P2,v2 重构:controller 原子作废)

**触发点 1 — 阶段推进门禁**(`check_workflow_stage_advance`,controller.py:721 附近):

现逻辑:reconcile → completed_nodes → `get_ready_nodes` → `mark_stage_advance_queued` → 投 `stage_advance` 事件。

插入:对每个 ready_node,检查其 `depends_on` 中门禁节点的 verdict;存在 `blocked` → **跳过推进**,执行原子作废序列(见下),投递一次 `fix_loop` 事件。legacy fallback 分支(无 nodes 的旧配置)同样插入门禁判断。

**触发点 2 — 工作流完成门禁**(`is_workflow_completed` 为真之后、`maybe_close_completed_workflow` 之前):最后节点 verdict=`blocked` → 不打 `[WORKFLOW COMPLETE]`、不 close,执行同样的作废序列 + `fix_loop` 事件。

**原子作废序列**(controller 执行,registry-only):

```
targets = [gate 节点的全部非 superseded 任务]
        + [retry_node 全部下游节点的非 superseded 任务]
for task in targets:
    if task.status in {"completed", "cleanup_ready"}:
        run(TASK_MANAGER finalize <task_id>)   # 幂等推进到 cleaned,绕开非法转移窗口
    run(TASK_MANAGER supersede <task_id> --reason "fix-loop: <gate stage> blocked")
```

- 转移合法性:cleaned→superseded 合法;completed/cleanup_ready 中间态先 finalize 规范化——**不这么做必然撞 exit 2**(TRANSITIONS:completed 只能去 committed/cleanup_ready);
- 作废后 gate 节点与下游节点全部回归未完成 → wrapup 不再 ready → 周期 sweep 不会再命中门禁(**节点未完成本身就是闩,无需新增 stage-state 锁位值**);reconcile 对已通知下游的 `notified` 吊销照常生效;
- superseded 不在 notifier 的 ATTENTION 集合 → 不会通知轰炸;registry/pane/clone 保留策略不变(pane_retained/clone_retained 尊重原值);
- 作废仅在状态实际发生变化时执行(幂等,重复触发 no-op)。

**fix_loop 事件**(单一动作,给总指挥):

```
HERDR_CONTROLLER_FIX_LOOP_EVENT

workflow_id / blocked_stage / verdict_note(blocker 清单)
retry_node: implementation
suggested_branch: <retry_node 最近 committed 任务的 branch 字段>
loop_count: N / max_loops: 3

Controller 已自动作废受影响的 gate 与下游 Task(见 registry)。
你现在只需:
~/herdr/bin/herdr-task launch --workflow-id <wf> --stage <retry_node> \
     --onto <suggested_branch> --agent auto \
     --goal "修复 <blocker>" [--supersedes <旧 fix task,如再次修复>]
fix task 完成后,Controller 将自动按 test → review → wrapup 顺序重新推进。
loop_count >= max_loops 时:先向用户请示(继续修/换方案/放弃),未经确认不得派发。
```

**防打转**:stage-state 记 `fix_loop_count[<wf>:<retry_node>]`,每次作废序列执行时 +1;达到 `max_loops` 后事件切换升级文案(见上)。上限是"纪律+通知"而非引擎硬闸——避免新增解锁命令与状态;notifier 的 blocked/failed 通道已覆盖人工提醒。

### C3 分支续接(原 P3)

**`herdr-task launch --onto <branch>`**:

- 语义:clone 后**不新建** agent 分支,fetch 并 checkout 既有分支;任务 commit 落在该分支 → 直接更新既有 PR(如 PR 1286 的 `agent/opencode/feat-impl-01-volume-mainchain`);
- 校验:`git fetch origin <branch>` 后分支必须存在,否则 fail-fast;与既有 `--base-branch` 语义互斥(`--onto` 的基线即该分支);
- **worker 顺序约束**:checkout 必须发生在 `build_baseline_fingerprint` 之前(worker 主流程:create_clone → 【checkout 或 create_task_branch】→ baseline → context),否则基线指纹会把 PR 分支的既有提交当成任务变更;
- registry `branch` 字段记该分支;`verify-baseline` / `commit` / finalize 流程不变;zero-exec 守卫对 fix task 自动生效;
- 兼容:不传 `--onto` 行为完全不变。

### C4 交付终态与重开(原 P4 + 收尾门禁)

**outcome 字段**(workflows.json entry):`delivered`(wrapup verdict=pass 后正常 close)| `abandoned`(显式放弃)。旧记录无 outcome = legacy,不回填。

**close-workflow 门禁升级**:

- 存在未 superseded 的 `blocked` verdict 任务时拒绝 close,提示使用新参数 `--abandon`(必须显式;记录 `outcome: abandoned`);
- 正常 close 记 `outcome: delivered`;
- `TEARDOWN_BLOCKING_STATUSES` 不变(结构闸门与语义闸门独立);
- **三处旁路全部封堵**:console `create_candidate`(真实 git merge,console:435)、console `manual_advance`(进入下一阶段按钮——现模板只有"请先检查门禁"的纪律约束)、`check_workflow_stage_advance` legacy fallback。凡存在 blocked verdict:console 两处拒绝执行(显式 force 除外),manual_advance 的消息模板附带 blocker 信息。

**`herdr-task reopen-workflow <wf>`**:

- 前置:entry.status 必须为 `completed`;coordinator pane 必须存活,否则拒绝并给出恢复指引;
- 动作:status → `"in_progress"`;置 `suppress_auto_close: true` **闩**;复用 `stage-reset` 内部逻辑重置阶段锁;
- **闩的必要性**(v2 新增):reopen 后旧任务全是完成系,`is_workflow_completed` 仍为 True,周期 sweep 下一次检查就会 auto-close → reopen 被自消除。闩的清除时机 = 该 workflow 任意任务进入 ACTIVE 状态(第一个 fix task 派发落盘,此时 implementation 节点已回归未完成,`is_workflow_completed` 自然为 False,闩可安全摘除);
- 已拆除的 clone/pane 不恢复——由后续 launch 的 `ensure_node_runtime` 按需重建;
- reopened workflow 全节点再次完成后,auto-close 正常触发(status 非 completed、闩已摘除)。

## 3. 与既有机制的交互清单(全盘排查结论)

| 机制 | 是否改动 | 说明 |
| --- | --- | --- |
| `is_node_complete` | 不改 | 作废/新任务使节点现算回归未完成 |
| `reconcile_stage_advance_states` | 不改 | 作废使节点未完成,吊销自然发生 |
| `handle_event` / zero-exec 守卫 | 不改 | 正交;fix task 自己也受守卫保护 |
| sentinel | 不改 | 只看 ACTIVE,fix task 天然被巡检 |
| notifier | 不改 | blocked/failed 已在 ATTENTION;superseded 不触发通知,作废无轰炸 |
| ops-center 统计 | 需回归 | fix loop 增加 supersede 用量,必须跑 TestOpsCardParity 门禁(4 处 supersede-exclusion predicate 一致性) |
| console `create_candidate` / `manual_advance` | 加门禁 | blocked verdict 拒绝(见 C4 三处旁路封堵) |
| stage-state | 只增计数器 | `fix_loop_count`;**不再需要** `fix_loop` 锁位值(v1 绕弯,已删) |
| 模板/schema | 更新契约 | `workflow-template-schema.md` 增 gate 配置;`template-authoring-guide.md` 增 verdict 验收话术;内置模板配默认 gate |
| 文档 | 更新 | wiki `dag-workflow-engine`、`task-lifecycle`、`log.md`;`cli-reference.md` 增 launch --onto / reopen-workflow / set --verdict;lessons 沉淀 |

## 4. 测试策略

1. **verdict 落盘**:`set --verdict` 合法三态(pass / blocked+note / 非法组合被拒)与字段持久化;
2. **阶段门禁**:blocked → ready_node 被拦 + 原子作废(gate+下游全部 superseded,含 finalize-first 路径)+ 单次 fix_loop 事件;**周期 sweep 重复检查不再重发**(节点未完成即闩);pass / 无 verdict(legacy)→ 照常推进;
3. **死循环回归**:模拟 fix 完成 → DAG 自动重流 test → review → 新 verdict=pass → wrapup 推进(门禁-作废-重流-再判定全链);
4. **完成门禁**:wrapup blocked → 无 `[WORKFLOW COMPLETE]`、无 close 调用;
5. **close 门禁**:blocked 拒绝 + `--abandon` 通路 + `outcome` 落盘;
6. **reopen**:前置校验;`suppress_auto_close` 闩生效(reopen 后 sweep 不误关)与摘除时机(首个 ACTIVE 任务);
7. **worker --onto**:分支存在性校验、跳过 create_task_branch、checkout 先于 baseline(mock subprocess,沿 test_workflow_finalize 模式);
8. **parity**:supersede 统计对齐门禁全量回归。

## 5. PR 切片与顺序

| PR | 内容 | 理由 |
| --- | --- | --- |
| PR-1 | C3 `--onto` + C4 `reopen-workflow`(含 suppress_auto_close 闩) | 小而独立;正在执行的 path A 立即受益(fix task 落 PR 分支) |
| PR-2 | C1 verdict + C2 fix-loop(原子作废)+ C4 收尾门禁(outcome / --abandon / 三处旁路封堵)+ 模板默认 gate + 全部文档 | 门禁语义一次闭环 |

部署:`launchctl kickstart -k` controller;console 改动走 `scripts/install-herdr-console.sh`。

## 6. 风险与开放问题

1. **循环上限非硬闸**(纪律+通知):接受吗?硬闸需新增解锁命令/状态,违背 G6;
2. **verdict 默认 lenient**(忘写放行+告警):模板可配 `require_verdict: true` 升级严格;
3. **两条集成路径并存**:fix-loop `--onto` 直更 PR 分支与 console `create_candidate` 汇聚候选分支,同一 workflow 二选一(判据:有无远端 PR),文档写明;
4. **总指挥忽略 fix_loop 事件 = 静默停**:与既有 stage_advance 通知同罪(依赖总指挥处理 prompt),不在本设计解决;后续可加 coordinator 停滞看门狗。

## 7. 本设计不解决的事(明确出界)

- 死会话派发的源头检测(dispatch 前会话存活校验)——zero-exec 守卫已兜底;
- PR 的创建/合并自动化——herdr 不碰远端 PR 生命周期,门禁只保证"不自动合、不误归档";
- 跨 workflow 的修复调度(同一功能多个 workflow 的关联视图)。

## 8. v1 → v2 对抗性审查记录(思维链)

| # | 级别 | 漏洞/绕弯 | 修订 |
| --- | --- | --- | --- |
| 1 | 致命 | **verdict 死循环**:fix 修完后 gate 节点仍 complete、旧 blocked verdict 仍被读到 → 门禁再次拦截 → 无限 fix_loop(v1 的锁只防重复投递,防不了语义死结;v1 模板 --supersedes 指向"旧 fix task"方向反了) | C2 原子作废:gate 任务+下游任务全部 supersede,节点回归未完成,DAG 结构性消灭死循环 |
| 2 | 重要 | **重测缺失**:test 节点旧任务仍完成系,DAG 从 implementation 直跳 review,总指挥要求的"fix→重测"不会发生 | 作废范围 = retry_node 全部下游(含 test/review/wrapup),整段重流 |
| 3 | 边界 | **作废撞非法转移**:stage check 可能在任务 completed/cleanup_ready 中间态触发,此时 supersede 被 TRANSITIONS 拒绝 | 作废 helper 先 finalize 规范化到 cleaned 再 supersede |
| 4 | 重要 | **reopen 自消除**:reopen 后旧任务全完成系,周期 sweep 下一次检查即 auto-close,workflow 秒回 completed | `suppress_auto_close` 闩,首个 ACTIVE 任务摘除 |
| 5 | 绕弯 | **总指挥负担过重**:v1 把"批量 supersede + launch"多步 SOP 写进事件模板,最易错环节压给执行者;还需 stage-state 扩展 fix_loop 锁位 | controller 原子作废取代 SOP;节点未完成即天然闩,锁位值删除 |
| 6 | 边界 | **门禁旁路**:console manual_advance 绕过 controller 门禁;legacy fallback 分支未覆盖 | 三处旁路封堵(create_candidate / manual_advance / legacy fallback) |

顺带确认的正确性边界:gate_verdict 改为 fail-safe 读法(任一未作废 blocked ⇒ blocked);--onto 的 checkout 必须先于 baseline 指纹;zero-exec 守卫对 fix task 自动生效;fix_loop 事件并发去重复用既有 queued_events。
