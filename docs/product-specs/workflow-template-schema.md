# 工作流模板规范定义 (Workflow Template Schema Spec)

> 本文档规范定义 Herdr Workflow Template (YAML / JSON) 的数据模型、字段契约、数据类型与校验边界。

---

## 1. 顶层根对象 Schema

| 字段 | 类型 | 是否必填 | 默认值 | 描述 |
| :--- | :--- | :--- | :--- | :--- |
| `name` | `string` | **是** | - | 模板全局唯一标识符，建议只包含小写字母、数字与连字符（如 `software-development-v1`）。 |
| `label` | `string` | 否 | 与 `name` 相同 | 人类可读名称，展示在 UI、通知与命令行界面中。 |
| `version` | `string` | 否 | `"1.0"` | 模板版本，用于后续模式升级与迁移。 |
| `description` | `string` | 否 | `""` | 模板的业务场景与流程概述。 |
| `nodes` | `List[Node]` | **是** | - | 工作流包含的节点数组（定义 DAG 的拓扑图）。 |

---

## 2. Node (工作流节点) Schema

| 字段 | 类型 | 是否必填 | 默认值 | 描述 |
| :--- | :--- | :--- | :--- | :--- |
| `id` | `string` | **是** | - | 节点在当前 Workflow 中的唯一 Key（如 `requirements`）。 |
| `label` | `string` | 否 | 与 `id` 相同 | 节点的中文/展示标签。 |
| `node_type` | `string` | 否 | `"agent"` | 节点执行类型。可选：`agent` (AI 智能体), `human` (人工节点), `tool` (脚本/程序), `gate` (条件网关)。 |
| `depends_on` | `List[string]`| 否 | `[]` | 当前节点依赖的前置节点 `id` 列表。为空表示起始根节点。 |
| `parallel` | `boolean` | 否 | `false` | 是否允许该节点内部同时并发派发并运行多个 Task。 |
| `purpose` | `string` | 否 | `""` | 节点的执行目的与职责简述，用于初始化 Coordinator 提示词。 |
| `default_integration_mode` | `string` | 否 | `"none"` | 产出物的合并模式：`none` (无需合并), `branch` (合并到分支), `commit` (提交)。 |
| `default_task_type` | `string` | 否 | `"docs"` | 默认任务类型，影响路由倾向：`explore`, `plan`, `code`, `test`, `review`, `docs`。 |
| `agent_policy` | `AgentPolicy` | 否 | `{}` | 该节点专有的 Agent 调度与路由约束配置。 |
| `required_outputs` | `List[string]`| 否 | `[]` | 该节点验收时必须存在的产出文件路径清单。 |
| `rules` | `List[string]`| 否 | `[]` | 该节点执行过程必须强制遵循的业务/工程规则。 |

---

## 3. AgentPolicy (节点级 Agent 策略) Schema

| 字段 | 类型 | 是否必填 | 默认值 | 描述 |
| :--- | :--- | :--- | :--- | :--- |
| `fixed` | `string` | 否 | `null` | 强制指定特定的 Agent 模型（如 `claude`），此时忽略自动路由。 |
| `preferred` | `List[string]`| 否 | `[]` | 偏好 Agent 候选列表，排在越前优先级越高。 |
| `exclude` | `List[string]`| 否 | `[]` | 坚决排除的 Agent 列表（例如排斥某类轻量 Agent）。 |
| `min_agents` | `integer` | 否 | `1` | 允许派发的最小 Agent 数量。 |
| `max_agents` | `integer` | 否 | `1` | 允许派发的最大并发 Agent 数量。 |

---

## 4. DAG 校验规则

所有模板在加载时必须通过 `validate_workflow_dag` 检查：
1. **唯一性校验**：所有节点的 `id` 必须全图唯一，不允许重名。
2. **合法引用校验**：所有 `depends_on` 中的节点 ID 必须在当前模板的 `nodes` 中存在。
3. **无环图校验 (Acyclic)**：基于 Kahn 算法进行拓扑检测，不能存在循环依赖（如 A ➔ B ➔ C ➔ A）。
