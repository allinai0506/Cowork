# 系统定位与业务全景 (system-overview.md)

> **系统概览与业务边界**  
> 关联索引: [[index]] | [[architecture]] | [[domain-model]] | [[tab-node-model]]

---

## 1. 核心定位与解决的问题

`FACT` **Herdr** 是基于 macOS 与 Herdr 终端多任务工作区的**本地 Multi-Agent Workflow Runtime 与 AI 研发工厂编排平台**。

系统解决的核心业务问题是：
> 如何让多种异构的 AI Coding CLI（如 Claude Code, Codex, OpenCode, QoderCLI, Agy, Pi）在本地真实项目中，按照声明式的组织结构与 DAG 工作流，在**环境安全隔离**、**现场可自愈**、**可审计验收**与**无人值守守护**的前提下协同完成端到端研发任务。

Evidence:
- `pyproject.toml:description` ("Herdr Multi-Agent Workflow Platform")
- `bin/herdr-factory`
- `services/herdr-controller.py`
- `docs/architecture/architecture-overview.md`

---

## 2. 系统的技术边界与依赖

```mermaid
graph TB
    subgraph External_Tools [外部执行与交互层]
        HerdrServer["Herdr Server (~/.config/herdr/herdr.sock)"]
        AgentCLIs["Agent CLIs (claude, codex, opencode, qodercn, agy, pi)"]
        MacOS["macOS 系统设施 (launchd, APFS cp -cR, osascript)"]
    end

    subgraph Herdr_Core [Herdr 控制与编排核心]
        CLI_Entry["CLI 入口 (herdr-factory, herdr-task, herdr-preflight)"]
        Services["LaunchAgent 常驻守护 (herdr-controller, herdr-sentinel, herdr-notifier)"]
        CoreLib["核心引擎 (workflow.py, agent_router.py, projects.py, topology.py)"]
    end

    subgraph State_Storage [运行时持久化与工作区 (~/.herdr-controller/)]
        StateFiles["JSON 状态中心 (tasks.json, projects.json, workflows.json)"]
        CoWClones["独立隔离工作区 (~/.herdr-controller/clones/<task-id>)"]
    end

    CLI_Entry --> CoreLib
    Services --> CoreLib
    CoreLib --> StateFiles
    CoreLib --> CoWClones
    CoreLib --> HerdrServer
    Services --> HerdrServer
    Services --> MacOS
    CoreLib --> AgentCLIs
```

### 2.1 边界内系统 (In-Scope)
- **调度协调层**: `services/herdr-controller.py` 负责监听任务变动、按 DAG 依赖推进阶段、向 Coordinator Pane 注入派发指令。
- **工作流算法层**: `herdr/workflow.py` 负责 Kahn 算法拓扑校验、模板解析与节点就绪计算。
- **空间自愈层**: `herdr/projects.py` 与 `herdr/topology.py` 保证无论终端窗格如何关闭，调度前自动修复 Tab 与 Anchor。
- **任务沙盒层**: `services/herdr-worker.py` 与 `bin/herdr-task` 负责基于 APFS CoW 创建瞬时隔离工作区，记录基线快照。
- **策略路由层**: `herdr/agent_router.py` 负责健康准入门禁、Node 级策略匹配与带 TTL 的 Reservation 锁。

### 2.2 边界外依赖 (External System)
- `FACT` **Herdr 终端服务**: 底层依赖外部启动的 `herdr status: running` 及其 Unix Domain Socket (`~/.config/herdr/herdr.sock`)。若 Herdr 未运行，大部分现场创建指令会失败。
- `FACT` **Agent CLI 二进制**: 本地必须安装对应的 CLI 工具并登录有效凭据（如 `~/.claude.json`, `~/.codex/auth.json`）。
- `FACT` **macOS Launchd**: 服务常驻依赖 `launchctl` 管理的 LaunchAgents (`com.user.herdr-controller` 等)。

Evidence:
- `herdr/preflight.py:AGENT_BINARIES`
- `services/herdr-controller.py:SOCKET_PATH`
- `CLAUDE.md:SERVICE`

---

## 3. 核心业务规则与工程红线

根据代码实现与 `RULES.md`，本系统具有以下不可逾越的规则：

1. `FACT` **空间隔离红线**: 任何针对代码的修改任务严禁在主干工作区直接执行，必须派发到独立的 CoW 克隆目录（`~/.herdr-controller/clones/<task-id>`）。
2. `FACT` **基线快照验收红线**: 任务验收严禁使用普通 `git status`，必须使用 `herdr-task verify-baseline <task-id>`，以任务创建时的树快照指纹为唯一对比基准。
3. `FACT` **逻辑标识高于运行时缓存**: 严禁将 Tab ID (`wA:t2`) 或 Pane ID (`wA:p3`) 作为持久业务标识。Tab ID/Pane ID 仅为运行时缓存，真正的业务事实是 Node ID/Label。
4. `FACT` **极简依赖原则**: 系统严格依托 Python 标准库与 `pyyaml`，坚决不引入庞杂的外部三方依赖框架。

Evidence:
- `RULES.md:空间隔离红线`
- `services/herdr-worker.py#create_clone`
- `bin/herdr-task#verify_baseline`
- `scripts/herdr-topology-selfheal-install.sh`

---

## 4. 关键认知与推论

- `INFERENCE` Herdr 的架构本质是一个“针对 AI Agent 的分布式作业调度操作系统”，将终端口视作物理工位，将 Agent 视作劳动力进程，将 DAG 视作业务生产管线。
- `UNKNOWN` 目前代码中 `services/herdr-controller.py` 顶部写死了默认的 `COORDINATOR_PANE = "w6:p1H"`，但在动态运行时会优先通过 `coordinator_pane_for_workflow` 获取真实项目 Pane。该写死值属于历史遗留或 fallback，需要关注是否在某些无 workflow_id 场景触发。

Evidence:
- `services/herdr-controller.py:COORDINATOR_PANE`
