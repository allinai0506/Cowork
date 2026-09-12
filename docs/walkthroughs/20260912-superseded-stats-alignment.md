# Implementation Notes — superseded 统计口径对齐 (2026-09-12)

> 本次 session 的运行笔记:记录规格(用户确认的修复方案)之外不得不做的决策、变更与权衡。
> 依 RULES.md「目录纯净红线」,本文件放在 `docs/walkthroughs/` 而非仓库根目录。

## 背景与规格

plan-t2 被 plan-t2-rev 取代(superseded)后,ops-center 节点卡片把 superseded 计入分母但不计入
completed,导致节点落到 pending、UI 显示"处理中",而 stage-status/controller 早已判定完成。
用户确认的方案:

1. `_node_task_status_counts` 统计前过滤 superseded(谓词与 controller/node_status 一致)。
2. console `stage_summary` 同步过滤,不再落 `mixed`("处理中")。
3. 补口径回归测试;console 经 `scripts/install-herdr-console.sh` 部署生效。

## 规格之外的决策

- **D1 谓词选型**:统一采用组合谓词 `status == "superseded" or superseded_by 存在`,
  与 `is_node_complete`(herdr-controller.py)和 `node_status`(bin/herdr-task)逐字对齐。
  后果:现网 plan-t2(cleaned + superseded_by)也会被卡片剔除,plan 节点从 3/3 变 2/2——
  这是把退役任务移出分母的预期效果,不是回归。
- **D2 全退役节点的展示语义**:节点全部任务被取代且无后继时,过滤后 total=0。
  新增节点状态 `superseded`("已取代")而不是让它伪装成 `empty`/`pending`,
  保证"被整体废弃的节点"与"无任务节点"可区分。
- **D3 stage_summary 的 count 语义**:`count` 改为存活任务数(剔除退役),
  详情页 `tasks` 列表仍返回全部记录(保留退役任务的可见性)。
- **D4 drilldown 顺带修复**:`_select_drilldown_task` 全终态回退原取 `tasks_for_node[0]`
  (最老记录),现改为优先取最新的权威任务(无 superseded_by)——否则下钻永远落在被退役的
  plan-t1 而非权威的 plan-t2-rev。
- **D5 笔记位置**:用户全局指令要求 implementation-notes,但 RULES.md 禁止仓库根目录新增文件,
  故落在本文件。
- **D6 humanStatus 无法直接单测**:它是 HTML_TEMPLATE 内嵌 JS,改用模板源码子串断言钉住映射
  (`superseded/in_progress/empty` 三条),属可接受的弱断言。
- **D7 不改 shared map 的 completed 文案**:节点徽章对 `completed` 显示"待收尾"沿用了任务级
  语义(任务 completed=待收尾),节点级略有歧义,但属既有文案决策,本次不顺带改,避免扩大爆炸半径。

## 变更清单

- `bin/herdr-task`:`_node_task_status_counts` 过滤 + superseded 计数;`_build_workflow_cards`
  节点/工作流级新增 superseded 聚合与 `superseded` 节点状态;`_select_drilldown_task` 终态回退修正。
- `console/herdr_factory_console.py`:`stage_summary` 过滤 superseded;`humanStatus` 新增
  `superseded:'已取代' / in_progress:'运行中' / empty:'无任务'`;CSS 新增 `.badge.superseded`、
  `.badge.in_progress`。
- 测试:`tests/test_herdr_task_ops_center.py` 新增 `TestSupersededStats`;
  `tests/test_stage_advance_and_supersede.py` 新增卡片与 `is_node_complete` 的口径一致性用例;
  `tests/test_console_stage_summary.py` 新建。
- 知识沉淀:`wiki/ops-center.md` 同步统计口径;`docs/lessons/lessons-learned.md` 新增口径漂移教训。

## 验证记录

- TDD 先红后绿:新用例先 13 failed,补丁后全量 `pytest tests/` → **101 passed, 12 subtests passed**。
- 真实登记表 `herdr-task ops-center --workflow-id wf-nexusarchive-54433229-20260912-194638`:
  plan 节点 `total:2 completed:2 superseded:1 status:completed`,drilldown 变为 `plan-t2-rev`;
  workflow 级 `total:12 completed:12 superseded:1`(修复前为混入退役任务的 13)。
- console 部署后实测(127.0.0.1:8765):
  `/api/ops-center` 同上,全节点 completed;
  `/api/workflow` 全部 stage 为 `cleaned`(修复前 plan 为 `mixed` → UI"处理中")。
- 知识沉淀:lessons-learned §7、wiki/ops-center.md §1 与 wiki/log.md 均已更新。
