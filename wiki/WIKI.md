# Wiki 维护规范与治理规则 (WIKI.md)

> 本规范定义 Herdr 仓库 LLM Wiki 的组织结构、证据契约、语法标准与持续维护工作流。  
> 所有在此代码库工作的 AI Coding Agent 与开发者必须遵守本文档规定的规则。

---

## 1. 核心定位与原则

1. **代码为唯一事实来源 (Code as Single Source of Truth)**:
   - Wiki 是对代码理解的结构化沉淀，不能脱离代码凭空推测。
   - 当文档与代码冲突时，以当前代码行为为准，并在 Wiki 中记录该不一致。
2. **知识分层与证据契约**:
   - 任何关键架构结论、状态流转、业务规则必须包含 `Evidence` 字段。
   - 引用真实的代码文件路径、函数名、类名、常量名或测试用例名（避免易失效的行号）。
3. **严格区分三种知识状态**:
   - `FACT`: 代码具有明确且直接的实现支撑。
   - `INFERENCE`: 基于多处代码行为推导出的架构设计意图或约束。
   - `UNKNOWN`: 当前代码库无法证实或逻辑意图存在断层，严禁捏造事实填补空白。

---

## 2. 页面组织与链接标准

### 2.1 命名与路径规范
- 所有 Wiki 页面存放于仓库根目录下的 `wiki/` 目录。
- 页面命名采用小写中划线格式：`wiki/<topic-name>.md`（如 `wiki/task-lifecycle.md`）。
- 入口索引固定为 `wiki/index.md`，演进日志固定为 `wiki/log.md`。

### 2.2 双向链接契约
- 页面间引用必须使用 `[[topic-name]]` 格式（如 `[[domain-model]]` 或 `[[task-lifecycle]]`）。
- 避免孤立页面（Orphans）：每个页面必须至少被 `wiki/index.md` 或相关领域页面引用一次。

### 2.3 Evidence 引用规范
证据段落格式统一如下：
```markdown
Evidence:
- `herdr/workflow.py#validate_workflow_dag`
- `bin/herdr-task:TRANSITIONS`
- `tests/test_workflow_engine.py#test_cycle_detection`
```

---

## 3. 持续维护触发准则 (Agent Lifecycle Sync)

未来 Coding Agent 在完成代码修改时，必须主动评估：
> **"Does this change invalidate or expand any existing Wiki knowledge?"**

### 3.1 必须同步更新 Wiki 的场景 (P0/P1)
- **状态机变更**: 修改了 `bin/herdr-task:TRANSITIONS` 或添加了任务生命周期状态。
- **调度与路由算法变动**: 调整了 `herdr/workflow.py` 的 DAG 拓扑计算或 `herdr/agent_router.py` 的路由优先级。
- **空间拓扑与自愈机制变动**: 修改了 `herdr/projects.py:ensure_node_runtime` 或 Tab/Anchor 管理逻辑。
- **持久化契约变更**: 调整了 `~/.herdr-controller/` 下的核心 JSON 结构（如 `tasks.json`, `workflows.json`）。
- **外部依赖与服务交互**: 修改了 LaunchAgent 守护机制或 Unix domain socket 交互。

### 3.2 严禁/无需更新 Wiki 的场景
- 代码格式化、Typo 修正、局部变量重命名、小型 Bug 修复（未改变宏观行为）。
- 易漂移的动态信息：禁止在 Wiki 记录总代码行数、commit SHA、文件数量、依赖包精确版本号等。

### 3.3 演进日志记录
任何对 Wiki 内容的结构性增删改，必须在 `wiki/log.md` 末尾追加一条记录，格式参考：
```markdown
## [YYYY-MM-DD] <action> | <brief reason>
- Updated [[page-name]]: summary of changes.
```

---

## 4. Wiki Lint 检查清单

在提交 Wiki 变更前，Agent 必须完成自检：
1. **Facts vs Code**: 检查新增的 FACT 是否与代码完全吻合。
2. **Broken Links**: 确认所有 `[[xxx]]` 维基链接的目标页面真实存在。
3. **No Orphans**: 确认新增页面已在 `wiki/index.md` 或相关页面中建立索引。
4. **No Slop**: 杜绝 AI 假话、套话和没有源码支撑的抽象泛化。
