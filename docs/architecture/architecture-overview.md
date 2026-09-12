# Herdr 系统全局架构 (Architecture Overview)

> 本文档描述 Herdr 多 Agent 编排系统的全局架构、分层设计、核心组件职责及数据流转路径。

---

## 1. 架构定位

Herdr Factory 是基于 Herdr 终端多任务工作区的**本地 Multi-Agent Workflow Runtime / AI 软件工厂控制层**。

系统解决的核心问题是：
> 如何让多种异构的 AI Coding Agent（Claude Code, Codex, OpenCode, QoderCLI, Agy, Pi 等）在本地真实项目中，按照声明式的组织结构与工作流，稳定、可审计、可自愈、可恢复地协同工作。

---

## 2. 系统分层架构

```mermaid
graph TD
    subgraph UI_Layer [1. 呈现与操作层 (Herdr UI / Terminal / Console)]
        HerdrWorkspace[Herdr Workspace: 项目工作区]
        Tabs[Tabs: 工作流节点现场]
        Panes[Panes: Agent 工位与终端]
        WebConsole[Herdr Factory Web Console]
    end

    subgraph Factory_Core [2. Factory 编排与控制层]
        WorkflowEngine[Workflow Engine: 模板与 DAG 依赖判定]
        Controller[Herdr Controller: 异步调度引擎]
        AgentRouter[Agent Router: 负载均衡与节点策略路由]
        SelfHeal[Self-Healing Runtime: Tab 与 Anchor 自动自愈]
        DeepPreflight[Deep Preflight: Agent 沙盒健康探针]
    end

    subgraph Storage_Layer [3. 注册与持久化层 (~/.herdr-controller/)]
        TasksDB[(tasks.json: 工单状态机)]
        ProjectsDB[(projects.json: 项目空间映射)]
        WorkflowsDB[(workflows.json: 工作流实例)]
        TemplatesDB[(templates/: YAML 模板仓库)]
        Locks[router.lock: 并发资源锁]
    end

    subgraph Agent_Layer [4. Agent 执行与适配层]
        Claude[Claude Code Adapter]
        Codex[Codex Adapter]
        OpenCode[OpenCode Adapter]
        Qoder[QoderCLI Adapter]
        Agy[Agy Adapter]
        Pi[Pi Adapter]
    end

    UI_Layer <--> Factory_Core
    Factory_Core <--> Storage_Layer
    Factory_Core --> Agent_Layer
```

---

## 3. 核心子系统与组件职责

### 3.1 工作流引擎 (`herdr/workflow.py`)
- **模板发现与加载**：扫描内置模板及用户自定义目录，解析 YAML / JSON。
- **DAG 依赖校验**：基于 Kahn 算法进行拓扑校验，阻断循环依赖与未知前置节点。
- **双向归一化**：负责通用 `nodes` 与兼容老版本的 `stages` 结构互相透明同步。
- **就绪判定 (`get_ready_nodes`)**：根据已完成节点集，实时推导当前可并发派发的新就绪节点列表。

### 3.2 控制器守护进程 (`services/herdr-controller.py`)
- **事件循环**：作为后台 LaunchAgent 运行，监控 `tasks.json`。
- **任务推进**：当某节点下任务完成（状态进入 `cleaned` 或 `completed`）时，结合 DAG 依赖动态推进至下一个节点。
- **协调器通知**：向 Coordinator Pane (`wX:p1`) 注入下一步的派发指令。
- **全流程终结**：所有节点完成时，触发系统级完成事件并唤醒 Notifier。

### 3.3 路由与负载均衡器 (`herdr/agent_router.py`)
- **策略继承**：优先解析并注入当前 Node 的 `agent_policy`。
- **健康约束**：仅从 Workflow Deep Preflight 验证健康的 `healthy_agents` 中挑选。
- **负载均衡**：基于并发任务数与带 TTL 的 Agent Reservation（锁预占）分摊负载。

### 3.4 运行时自愈系统 (`herdr/projects.py`)
- **逻辑事实 vs 运行时映射**：模板定义是逻辑事实，Tab/Pane ID 仅为运行时映射。
- **`ensure_node_runtime`**：在每次任务派发前动态探活 Tab 与 Anchor Pane。如遇人为误关，毫秒级自动补全，避免运行时报错。

### 3.5 隔离与沙盒层
- **CoW (Copy-on-Write) 工作区隔离**：Task 执行在独立 git clone / branch 目录中，保护主干代码。
- **现场保留**：Agent 执行完毕后现场默认保留供审计，仅在明确指令下进行逻辑清理与物理归档。
