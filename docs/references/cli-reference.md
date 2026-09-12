# CLI 全量命令参考手册 (CLI Reference)

> 本文档列出 Herdr 编排系统中所有控制台与命令行工具的完整语法、选项及返回值说明。

---

## 1. `herdr-factory` 命令

项目与工作流生命周期控制工具。

### 1.1 `herdr-factory templates`
列出所有可用的工作流模板（包含内置与用户目录）。
```bash
herdr-factory templates
```

### 1.2 `herdr-factory run`
启动一个新的工作流实例并通知协调员。
```bash
herdr-factory run <requirement...> [--template <name>] [--project <path>] [--workflow-id <id>] [--agent <name>]
```
- `<requirement...>`：自然语言业务需求描述。
- `--template <name>`：指定工作流模板，默认 `software-development-v1`。
- `--project <path>`：指定本地 Git 仓库路径，默认当前所在目录。
- `--agent <name>`：强制指定全局 Agent，默认 `auto`（走路由器）。

### 1.3 `herdr-factory status`
展示指定 Workflow 各节点的执行状态。
```bash
herdr-factory status <workflow_id>
```

### 1.4 `herdr-factory doctor`
运行全局环境自检（Herdr 服务、Agent 探针、Git 状态等）。
```bash
herdr-factory doctor
```

---

## 2. `herdr-task` 命令

工单（Task）创建、生命周期与节点工位自愈工具。

### 2.1 `herdr-task launch`
创建并立即在目标节点派生工位执行 Task。
```bash
herdr-task launch \
  --workflow-id <id> \
  --node <node_id> \
  --goal "<任务目标>" \
  --criteria "<验收标准>" \
  [--task-type explore|plan|code|test|review|docs] \
  [--agent auto|<agent_name>] \
  [--source <project_root>]
```
> 注：`--stage` 可作为 `--node` 的兼容别名。

### 2.2 `herdr-task node-status`
查询 Workflow 中所有节点的 DAG 依赖、Tab/Anchor 映射及关联任务。
```bash
herdr-task node-status --workflow-id <workflow_id>
```

### 2.3 `herdr-task ensure-runtime`
对目标节点执行 Tab / Anchor Pane 的探活与自动自愈。
```bash
herdr-task ensure-runtime --workflow-id <workflow_id> --node <node_id>
```

### 2.4 `herdr-task status` / `list` / `cleanup`
```bash
# 查看任务状态
herdr-task status <task_id>

# 列出当前工作流的所有任务
herdr-task list --workflow-id <workflow_id>

# 清理已完成任务的现场 Pane 与工作区分支
herdr-task cleanup <task_id>
```

---

## 3. `herdr-preflight` 命令

Agent 本地环境快速检测与准入控制工具。
```bash
# 检查当前项目或所有 Agent 的基础状态
herdr-preflight

# 输出 JSON 格式
herdr-preflight --json

# 临时禁用某 Agent
herdr-preflight --disable pi
```

---

## 4. `herdr-deep-preflight` 命令

沙盒化真实 Provider 探活与深层探针工具。
```bash
# 执行深层探针检测
herdr-deep-preflight --deep

# 执行检测并在发现硬故障时自动剔除
herdr-deep-preflight --deep --auto-disable
```
