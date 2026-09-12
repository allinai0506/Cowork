# 20260912 Console 工作流模板库（页面编排）Walkthrough / Implementation Notes

> 任务：把"自由编排工作流"从纯 YAML 入口补齐到共事工厂控制台页面——模板可见、可选、可在页面上编排保存（用户确认的方案 1+2）。本文同时是 implementation-notes：记录规格外决策、变更与权衡。

---

## 任务目标与背景

- **问题**：工作流模板（定义层）只有 YAML 一条路；控制台没有任何模板 API，"新建需求"启动时也不传 `--template`，永远落在默认 `software-development-v1`（console:316-318 + herdr-factory:267）。bidding / customer-service 及自定义模板在前端"看不到也选不了"。
- **范围（与用户对齐）**：①模板列表 + DAG 预览 API + 新建需求模板下拉透传；②页面表单编排自定义模板（服务端 DAG 校验，写入 `~/.herdr-controller/templates/`）。**明确不做**：修改运行中 workflow 实例的 DAG 结构。

## 改动范围

| 文件 | 变更 |
| :--- | :--- |
| `console/herdr_factory_console.py` | 后端：`templates_summary` / `template_detail` / `save_template` + 3 条路由；`run_workflow`/`start_workflow_job`/`_run_workflow_job` 增加 template 参数；前端："模板库"按钮、模板列表/节点依赖/YAML 编辑/新建模板弹窗、新需求下拉框 |
| `tests/test_console_templates.py`（新增） | 19 项验收测试（RED-first：先写先败，后实现转绿） |
| `console/README.md` / `docs/guides/universal-workflow-guide.md` | API 与用法文档同步 |

## 规格外决策与权衡（implementation-notes）

1. **直接 import `herdr.workflow`，而非给 CLI 加 JSON 子命令**。理由：CLAUDE.md 坑点 5 本就规定 `sys.path` + `from herdr.xxx import` 模式；复用 `list_templates / load_template / validate_workflow_dag` 零重复（Ponytail 原则）。权衡：部署副本 `~/.herdr-console` 因此依赖 `~/herdr` 存在——这是既有事实（deep_preflight 等已同此依赖）。
2. **保存语义 = 先校验后写盘**。若"先写盘再校验"，损坏模板会留在目录里，而 `list_templates` 对坏文件是静默跳过的——模板会从列表里无声消失，比拒绝保存糟得多。校验失败一律不落盘。
3. **模板名白名单 `^[a-z0-9][a-z0-9_-]{0,63}$`**：name 直接拼文件路径，必须防路径穿越（`../evil` 直接拒绝）；同时拒绝与内置模板重名（内置只读，且 `list_templates` 是用户目录优先，重名会遮蔽内置）。
4. **YAML `name:` 字段与文件名一致性校验**：`load_template` 按文件名 stem 查找，`name` 只影响列表显示；不校验会出现"列表显示名 ≠ --template 参数"的困惑。
5. **`validate_workflow_dag` 抛 `ValueError`，save 统一转 `RuntimeError`**：HTTP 层把 `Exception` 包成 500 + 文案，前端 toast 直接展示；与既有保存类接口错误约定一致。
6. **前端零路由扩展，全走现有 modal 体系**；DAG 预览用"节点 + ← 依赖"列表而非画图（无图库依赖，信息密度足够）。新建模板预填双节点脚手架，避免空白编辑器。
7. **双提交路径都补 template**：`submitNewWorkflowAsync` 才是实际生效路径（capture 拦截器使同步版 `submitNewWorkflow` 不可达）；为防未来移除拦截器时丢参数，同步版一并补上。**发现（未处理，超范围）**：同步版实为死代码，可考虑后续清理。
8. **实施失误记录**：一次 Edit 误删了 newAgent 的 `auto` 选项，紧接着的检查（grep 确认标记唯一性）发现并当即修复；最终测试覆盖了弹窗结构。

## 范围外发现（只记录，不修改）

- `workflow_detail`（console:298-304）与 `manual_advance` 仍按固定 6 阶段 `STAGES` 渲染/推进，对 bidding / customer-service 等非标 DAG 显示不真实。属独立缺陷，建议单独立项（ops-center 已按真实 nodes 展示，可参照）。

## 验证与测试数据

- 基线（改前）：`pytest -q` → **57 passed, 9 subtests**（注：CLAUDE.md §3 "16 个测试" 已过时，现库 57 项）。
- RED：新增 `tests/test_console_templates.py` 19 项先跑 → 19 failed；实现后全绿。
- 全量（改后）：`pytest -q` → **76 passed, 9 subtests**，零回归；`compileall` 全目录通过。
- 实机冒烟（临时端口 8799）：`GET /api/templates` 返回 3 内置模板；`GET /api/template?id=bidding` 返回 7 节点与 strategy 汇聚依赖；`POST /api/template` 合法 YAML 落盘成功、循环依赖被拒（ok:false + 原因）；页面 HTML 命中 7 处新挂载点。冒烟临时模板已清理。

## 部署

仓库内 canonical source 改动经 `./scripts/install-herdr-console.sh` 同步至 `~/.herdr-console` 并重启 `com.user.herdr-factory-console` LaunchAgent 后，在 http://127.0.0.1:8765/ 生效。

---

## 追加交付（同日）

1. **`b10b117`**：新需求模板下拉默认预选 `software-development-v1`（原 `populateTemplateSelect` 按字母序重写选项，默认悄悄变成 bidding）。
2. **术语翻译**：用户确认映射 Tab=工作流节点/阶段、Pane=智能体工位、Agent=执行者、Task=任务，已落实前端全部用户可见文案（约 25 处，RED-first 补 `TestTermTranslation` 断言新旧两侧）。编辑决策：标题与空态用全称（如"常驻智能体工位"），紧凑场景（按钮、行内提示）用短形"工位/节点"；Workflow、Space、`auto`、Router、agent 名称与状态枚举值按用户未列出而保持原样，README 术语约定已同步改写。`auto（Router 自动）` 中的 Router 属组件名未翻译。
