# 20260913 Controller 幽灵推进根治 + 创建闸门 + Console 反馈闭环

> 关联事故：wf-nexusarchive-54433229-20260913-111426 幽灵推进（已关闭工作流被
> controller 逐阶段真空推进，协调者 Pane 被注入幽灵提示并派发 3 个真实任务）。
> 本文记录修复实现中的决策、权衡与计划外事件。教训四段式沉淀见
> `docs/lessons/lessons-learned.md` §13。

## 1. 交付范围（用户确认的 P0-A / P0-B / P1-C + 运维卫生）

| 项 | 内容 | 落点 |
|---|---|---|
| P0-A | 推进扫描只遍历非终态条目；消费线程 fire 前逐迭代再校验 | `services/herdr-controller.py` |
| P0-B | 同项目活跃工作流检查 + flock 原子化 + `--force` 逃生口 | `bin/herdr-factory` |
| 共享谓词 | `workflow_closed` / `workflow_registered` / `non_terminal_workflow_ids` / `active_workflows_for_project` / `workflow_creation_lock` | `herdr/projects.py` |
| P1-C | 创建成功解析 `WORKFLOW_ID=` 自动切换视图；等待期逐秒耗时 + 防重复提示 | `console/herdr_factory_console.py` |
| 卫生 | legacy 顶层 workflow.json 归档；24 个僵尸 stage-state 键清除；125332 测试残留移除 | `~/.herdr-controller/` |

## 2. 关键决策与权衡（规格未写、实现时定的）

1. **终态谓词收敛到 `herdr/projects.py`** 而非各自内联：controller 扫描侧、
   消费侧、factory 闸门三处共用同一套判据，杜绝四实现漂移（§10 教训的翻版）。
   终态集合 `TERMINAL_WORKFLOW_STATUSES = {"completed"}`——注册表缺 status 键
   一律视为活跃（保守方向：宁可误拒不可误放）。
2. **消费端再校验放在 while 循环内、每次迭代执行**：111426 事故里已入队事件
   在注册表条目删除后仍触发了注入——事件从入队到 fire 之间可间隔分钟级，
   只有逐迭代重校验能关掉这个 TOCTOU 窗口。代价：每秒一次 JSON 读，量级可忽略。
3. **`workflow_registered` 为 False 也丢弃事件**：条目被移除（运维清场配方）
   等价于注销，事件继续 fire 只会注入无主提示。潜在代价：未来若出现
   "注册表在 projects.json、workflows.json 无条目"的 legacy 工作流会被误丢——
   当前生产无此形态（projects.json 条目均无 workflow_id 字段），接受。
4. **e2e 自动 bypass 创建闸门**：`force=force or e2e`。e2e 是系统自测流程，
   要求其加 `--force` 会破坏自动化。
5. **拒绝走 `SystemExit(2)` + stderr 友好消息**，而非异常栈：console 的
   `run_workflow` 取 returncode + stderr 直接呈现给用户；复用 preflight 失败
   的既有通道，不新造错误协议。
6. **flock 锁文件放 `~/.herdr-controller/locks/`（新目录）**：注册表本身不适宜
   做锁载体（读写频繁）；锁只包「检查 + 注册」两步（毫秒级），preflight 在锁外。
7. **保留对方会话的重复防御**：fix-loop 会话在 `check_workflow_stage_advance`
   中段也加了一处 completed 早退（针对 abandoned verdict 回流）。我方顶部
   早退与其功能重叠但依据不同（不依赖对方未提交代码堵 P0），两处并存，
   待其合流后可去重。
8. **运维卫生未做 Git 化**：`~/.herdr-controller/` 是运行时状态目录，归档/清理
   直接以文件操作完成，备份留 `backups/`。

## 3. 计划外事件：并行会话 Git 碰撞（重要）

本 session 与另一 fix-loop 会话**同时**在同一 working tree 工作：

- 我完成 controller/factory 编辑后，对方以 `git add -A` 式提交
  （e12d41d）把我的未提交改动一并扫进其 "review-round-1" 提交；
- 随后对方 `reset --hard` + `commit --amend`（e12d41d → 5c85442），
  **抹掉了我的 b9f6564 提交与工作区的 projects.py/factory 改动**；
- 中间窗口 HEAD 两次处于断裂态（已提交代码 import 未提交函数）；
- 运行中的 controller 因启动早于洗盘而幸存，且在真实环境完成了对测试
  工作流 125332 的「真空完成 → 干净自动关闭」——**P0-A 的正面活体证据**；
- 处置：从 reflog（b9f6564、e12d41d）恢复全部代码 → 全量测试 →
  单独提交 a4355aa 恢复 HEAD 自洽。

**教训**：单 working tree 双会话并发写 + 历史改写，等于互相丢工作。
后续建议：并行会话各自使用独立 worktree（`git worktree`），或约定
"谁在分支上谁提交"。

## 4. 活体验证记录

| 验证 | 结果 |
|---|---|
| 单测 | `pytest tests/` 183 passed, 12 subtests（含新增 `tests/test_workflow_registry_guards.py` 6 条） |
| 创建闸门 | `herdr-factory run --project ~/nexusarchive …`（111049 活跃时）→ exit 2，拒绝消息列出活跃工作流与主题，注册表零新增 |
| 幽灵消除 | 测试工作流 125332（零任务）被新 controller 判定 `[WORKFLOW COMPLETE]` → 自动 close-workflow → `status=completed`，日志无任何 STAGE ADVANCE 循环 |
| Console | install-herdr-console.sh 部署后 8765 端口 HTML 含 `runWaitStatus`（×3）与 `WORKFLOW_ID=` 解析逻辑 |
| Controller 重启 | `launchctl kickstart -k`（RULES §2 合规）后 state=running，111049 任务事件正常恢复订阅 |

## 5. 已知残留 / 后续项

- `check_workflow_stage_advance` 中段与我方顶部早退重复（见 §2.7），对方
  会话合流后去重。
- 消费端 drop 目前对「注销」与「已关闭」同日志处理，如需区分可拆分。
- Console 的 `submitNewWorkflow()`（旧同步版函数）仍留在代码里成为死路径，
  下次 console 迭代可清理。

## 6. 后记：第二次碰撞与恢复（13:0x）

§3 记录的碰撞并非最后一次：对方会话随后再次 reset/amend（d9a22cb）+ 提交
自己的知识同步（8291aee），我的 a4355aa/09b0d8d 第二次被冲掉，console 前端
修复（从未进过提交）连同部署被回退。处置：

1. 从备份分支 `backup/ghost-advance-and-create-guard`（钉在 09b0d8d）恢复：
   `cherry-pick a4355aa`（代码，现 7ce9b45）→ 重放 console 三处编辑（94d1515）
   → `cherry-pick 09b0d8d`（文档，冲突手工合并：对方 §12 保留、本文 §13、
   wiki log 两条并存，现 88584b5）；
2. 合并后事实对齐：controller 侧终态闸门由对方以内联方式独立实现
   （sweep 过滤 + 本地 `workflow_closed` + 消费端再校验），factory 闸门与
   共享谓词由我方提供——功能闭环，但终态语义存在两份实现（见 §13 规范 1），
   口径变更需两处同步；
3. 已在对话中请用户向对方会话传达协作协议（禁改写共享分支历史、禁 add -A
   扫荡式提交、分 worktree）。
