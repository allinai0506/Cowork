# Wiki Evolution Log (log.md)

> 本文件为 Herdr 知识层的 Append-Only 演进记录。  
> 仅记录 Wiki 结构与知识库发生实质性变更的原因与概要，不记录细碎的代码提交流水。

---

## [2026-09-12] init | Initial repository analysis & Wiki creation
Created initial LLM Wiki directly derived from active repository code inspection and behavioral evidence.
- Established Wiki Governance Rules in [[WIKI]].
- Designed master navigation and domain routing index in [[index]].
- Captured high-level architecture, problem boundaries, and core topology in [[system-overview]] and [[architecture]].
- Formulated core business domain entities and persistent storage contracts in [[domain-model]].
- Captured the critical Tab = Node spatial paradigm, Anchor Pane split mechanism, and runtime dynamic self-healing engine in [[tab-node-model]].
- Codified the full 11-state task lifecycle, CoW Git clone isolation, and baseline fingerprint verification in [[task-lifecycle]].
- Documented DAG topology parsing, cycle detection via Kahn's algorithm, and node ready resolution in [[dag-workflow-engine]].
- Documented multi-agent load balancing, reservation locking, and policy-driven routing in [[agent-routing-and-pools]].
- Documented non-destructive lightweight and deep sandbox health probes in [[preflight-and-health]].
- Codified developer & agent guide for safely making frequent codebase modifications in [[common-change-paths]].

## [2026-09-12] add | Agent Operations Center knowledge
- Added [[ops-center]]: documented the four dashboard layers, runtime/task state distinction, duration buckets, trajectory output, and anomaly action contract.
- Updated [[index]]: indexed the new operations view for future code navigation.

## [2026-09-12] move | Console source into repository
- Added the canonical Console source under `console/` and a repeatable `scripts/install-herdr-console.sh` deployment path.
- Updated service operations documentation and [[ops-center]] to distinguish repository source from the LaunchAgent deployment copy.

## [2026-09-12] fix | Workflow deadlock permanent engineering fix

Root-cause analysis identified three compounding failure modes causing DAG advance to permanently stall:

1. **`failed` status as permanent blocker** — `is_node_complete` had no way to skip a task that was replaced by another attempt; a single `failed` task would prevent the entire node from ever completing.

2. **`stage-state.json` write-once latch** — once `notified` was written for a stage, `mark_stage_advance_queued` would refuse to re-queue it even after the predecessor node regressed (e.g., new `failed` tasks arrived). The Controller would never re-trigger the advance.

3. **Head-of-Line blocking in `coordinator_worker`** — a single thread handled all workflows sequentially. One coordinator blocked on a busy agent would stall all other pending workflow advances indefinitely.

### Changes made

- **`bin/herdr-task`**:
  - `TRANSITIONS`: added `superseded` as a valid exit from `failed`, `cleaned`, and all in-progress states (`dispatched`, `working`, `blocked`, `agent_done`, `rework`). `superseded` is a terminal state.
  - `node_status` / `is_node_complete`: active tasks are now computed excluding `superseded` ones. A node with only superseded tasks and no active replacements is `incomplete`.
  - New `supersede_task()` function: marks a task `superseded`, optionally linking `superseded_by` and `supersede_reason`.
  - New `supersede` CLI subcommand: `herdr-task supersede <task_id> [--by <new_id>] [--reason ...]`
  - New `--supersedes` flag on `launch`: atomically supersedes the old task before launching the replacement in a single command.
  - New `stage-reset` subcommand: clears `stage-state.json` advance locks for a workflow (optionally scoped to a single stage).
  - New `advance` subcommand: calls `stage-reset` then prints a confirmation that the Controller will re-evaluate within ~2 s.

- **`services/herdr-controller.py`**:
  - `is_node_complete`: mirrors the `bin/herdr-task` logic — excludes `superseded` / `superseded_by` tasks.
  - `reconcile_stage_advance_states()`: called at the start of every `check_workflow_stage_advance` cycle. Scans `stage-state.json` for `notified` entries whose predecessor nodes are no longer complete, and revokes them so the advance can be re-triggered on the next cycle (~2 s).
  - `coordinator_worker` refactored to a lightweight dispatcher using `ThreadPoolExecutor(max_workers=16)` with per-workflow serialization locks (`_workflow_dispatch_lock`). Each workflow's blocking prompt call runs in its own executor thread, eliminating HoL blocking across workflows.

- **`tests/test_stage_advance_and_supersede.py`**: 17 new regression tests covering all five engineering changes. Full suite: 33 passed, 0 regressions.

## [2026-09-12] fix | Superseded-task stats alignment across ops-center and console

Root cause: the supersede exclusion predicate existed in 4 hand-written copies
(`is_node_complete`, `node_status`, `_node_task_status_counts`, console
`stage_summary`); the supersede feature synced only the first two, so the ops
board counted superseded tasks in the node denominator (→ pending) and the
console detail page fell to `mixed` (→ 处理中) while the controller had
already advanced the DAG.

- Updated [[ops-center]] §1: node/workflow `total` now counts only live tasks
  (`status == "superseded" or superseded_by` excluded, counted separately);
  fully retired nodes surface a distinct `superseded` status; drilldown picks
  the latest authoritative task when all tasks are terminal.
- Automation gate added: `tests/test_stage_advance_and_supersede.py#TestOpsCardParity`
  pins card aggregation to `is_node_complete` (this stats-drift class recurred
  for the 2nd time, per lessons-learned discipline #4).
- Lessons recorded in `docs/lessons/lessons-learned.md` §7.

## [2026-09-12] fix | Agent CLI binary resolution unified into herdr/agent_binary.py
Console roster / lightweight preflight / deep preflight each hand-rolled the
agent-id -> CLI mapping and resolved binaries via bare `shutil.which`, which
misses volta / `~/.local/bin` / `~/.qoder-cn/entry` installs under the
LaunchAgents' minimal PATH (codex/claude/qodercli/agy shown 未安装 while installed).
- Added [[preflight-and-health]] §2: resolution order is now
  `shutil.which` -> `EXTRA_BIN_DIRS` fallback -> login-shell `command -v`.
- Updated [[common-change-paths]] §2/§3 + [[index]] routing row: new-agent
  registration now starts at `herdr/agent_binary.py` (single source of truth
  for `AGENT_BINARIES`); preflight/deep_preflight keep only `AUTH_HINTS`/`VERSION_ARGS`.
- Lessons recorded in `docs/lessons/lessons-learned.md` §8 (3rd recurrence of
  the same-semantics-multi-implementation class).

## [2026-09-13] feat | Physical teardown lifecycle: finalize / close-workflow
Workflow 完成后任务 pane/clone 永不销毁(pane_persistent 默认保留),上下文随
活体无限累积;dispatch 前的 `/clear` 因 `_claimed_panes` 永久占用 pane 而结构性
空转(pane 复用从未发生)。确立"生而隔离,死而清零"生命周期并落地:
- Added [[task-lifecycle]] §5:finalize 序列(证据转写先行 → pane close →
  分档 clone 删除 → 状态推进)与 close-workflow 批量收尾(活跃闸门 /
  failed 保留 / 共享 tab 外来 pane 守卫 / 总指挥 pane 保留至知识沉淀后)。
- Controller 在 `[WORKFLOW COMPLETE]` 自动触发 close-workflow(防重入);
  purge 门槛放宽到非 ACTIVE(修 superseded 终态无法 purge 的死锁)。
- 新命令文档见 `docs/references/cli-reference.md` §2.6/2.7;决策与权衡
  (含上线当天抓到的共享 tab 连带销毁 bug)详见
  `docs/walkthroughs/20260913-workflow-finalize.md`;教训沉淀 §9。
- 验证:134 tests passed;真实端到端——wf-…-232500 手动收尾 + 历史 workflow
  自动收尾,9/9 workflows completed,pane 24→1。

## [2026-09-13] feat | Fix-loop: gate verdicts, atomic invalidation, delivery-outcome gates
评审"不通过"原先只活在自然语言里,交付被阻断的 workflow 仍被归档 completed
(wf-nexusarchive-…-084418 复盘)。本次落地 fix-loop 设计 v2(经对抗性思维链
审查修订,见 docs/walkthroughs/20260913-fix-loop-design.md §8):
- Added [[task-lifecycle]] §1.1 + [[dag-workflow-engine]] §10:门禁阶段
  (test/review/wrapup)落盘 `stage_verdict`,`blocked` 触发 controller 原子
  作废(gate+下游,finalize-first 规避非法转移窗口)并派发 fix_loop 事件,
  fix 完成后 DAG 自动重流;交付终态门禁阻止 blocked workflow 被关闭。
- `launch --onto`(commit 直落 PR 分支)、`reopen-workflow`(suppress_auto_close
  闩防 sweep 自消除)、`close-workflow --abandon`(outcome 语义)、console
  create_candidate/manual_advance 门禁封堵一键合并旁路。
- 教训 §12:流程完成≠交付完成;审查轮 1 抓到 verdict 死循环/重测缺失/
  reopen 自消除三处设计级漏洞后修复。
- 验证:183 tests passed;独立审查两轮。

## [2026-09-13] fix | Terminal-state gates: ghost-advance elimination + duplicate-creation guard
已关闭/零任务工作流被 controller 逐阶段"真空推进"并向共享协调者 Pane 注入幽灵
提示，协调者照办派发了 3 个真实任务（wf-…-111426 幽灵事故）；同项目重复创建
无任何防护。三层落地：
- [[architecture]] §2.1 增补终态闸门与创建闸门两条 FACT：推进扫描只遍历
  非终态条目 + 消费线程 fire 前再校验；herdr-factory 注册前 flock 内原子
  执行「同项目活跃检查 + 注册」，`--force` 为唯一逃生口。
- 共享谓词收敛到 `herdr/projects.py`（`workflow_closed` 等 5 个），factory
  消费；controller 内联同语义实现；单测 `tests/test_workflow_registry_guards.py`。
- Console 创建反馈闭环：成功后解析 `WORKFLOW_ID=` 自动切换到新工作流视图，
  等待期显示耗时与"请勿重复创建"提示。
- 运维卫生：legacy 顶层 workflow.json（09-11 e2e 残留，registry-less fallback
  复燃路径）已归档；24 个 e2e/测试 stage-state 僵尸键清除。
- 活体验证：创建闸门 exit 2 拒绝 + 注册表零新增；测试工作流 125332 被新版
  controller 判定完成并干净自动关闭（无幽灵推进）。教训沉淀 §13；决策与
  会话碰撞记录见 `docs/walkthroughs/20260913-controller-ghost-advance-and-create-guard.md`。
