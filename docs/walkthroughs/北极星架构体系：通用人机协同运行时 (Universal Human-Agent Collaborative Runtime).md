# 北极星架构体系：通用人机协同运行时 (Universal Human-Agent Collaborative Runtime)

> **产品定位**：一个让人类与多个 AI Agent 共同完成复杂工作的**可编排、可干预、可恢复运行时**。  
> **演进原则**：North Star Architecture（北极星指引方向，开发由真实痛点单点逼出）。

---

## 1. 核心架构哲学 (Design Philosophy)

1. **认知平权，权限分级 (Peer Collaboration, Asymmetric Authority)**：
   * **认知协作层（平权）**：Human ↔ Agent 对等对话，可互相质询、提供替代方案、发起澄清。
   * **权限控制层（分级）**：Human > Agent，高风险动作（数据销毁、支付操作、上线发布、合入主干、签署对外协议等）必须受控于人类显式门禁。
2. **配置优先，代码可扩展 (Configuration-First, Code-Extensible)**：
   * 工作流拓扑、节点依赖、执行者策略、常规门禁声明在 YAML 中。
   * 复杂业务验证、特殊数据转换、动态判定逻辑通过插件代码（Plugin Handlers）扩展，杜绝将 YAML 异化为伪编程语言。
3. **确定性事实与 Agent 语义双轨制 (Deterministic Facts + Agent Semantics)**：
   * **系统事实（Source of Truth）**：Tool 触发、文件修改、测试结果、Git 提交由底座确定性生成与捕获，不依赖 Agent 自觉。
   * **Agent 语义（Cognitive Layer）**：关键洞见、决策取舍理由、潜在风险由 Agent 主动调用协议呈报。
4. **注意力驱动呈现 (Attention-Driven Observability)**：
   * 人类的注意力是系统中最昂贵、稀缺的资源。
   * 系统不要求人盯视全量 Agent，而是通过结构化状态过滤：**“30 个 Agent 正在工作，3 个需要关注，1 个需要你拍板”**。
5. **产物（Artifacts）第一公民化**：
   * 价值的终局不是聊天过程，而是交付物（报告、数据表格、合规结论、代码分支、方案 PPT）。产物作为系统一等实体。

---

## 2. 系统五层核心架构 (5-Layer Architectural Stack)

```mermaid
graph TD
    subgraph Layer1 [1. 人机协同视窗 (Human Collaboration UI)]
        AttentionHub[注意力中心: 告警 / 关注 / 待拍板决策]
        SteerDock[干预控制台: 夺回调度权 / 队列插话 / 单步调试]
        ArtifactGallery[交付物会签室: 报告 / 表格 / 成果审批]
        DeepDrawer[折叠物理抽屉: 原生终端与底层现场]
    end

    subgraph Layer2 [2. 语义投影与提炼层 (Projection & Telemetry)]
        FactCollector[系统确定性事实采集器: Tool / FS / Git / Gate]
        InsightParser[Agent 语义提炼器: 核心洞见与决策归因]
        AttentionEngine[注意力评估引擎: requires_attention & severity]
    end

    subgraph Layer3 [3. 工作流调度内核 (Workflow Kernel)]
        DAGScheduler[通用 DAG 拓扑推进引擎: Node & Transition]
        CheckpointStore[(Durable Checkpoints: V1 Append-Log -> V2 SQLite)]
        GateEngine[通用门禁规则评估器: 声明式 + Plugin Handlers]
        RollbackRouter[状态回滚与 Fix-Loop 状态机]
    end

    subgraph Layer4 [4. 执行者适配与路由层 (Agent Runtime Abstraction)]
        AgentRouter[节点级策略路由与并发 Reservation]
        PreflightProbe[健康准入与沙盒探针: Deep Preflight]
        AdapterMatrix[Agent Adapter 矩阵: 声明能力支持字典]
        SteerBus[双轨调度总线: Hard Halt & Queued Steer]
    end

    subgraph Layer5 [5. 能力、权限与沙盒层 (Capability & Sandbox)]
        MCPRegistry[MCP 工具协议连接器]
        ToolAdapter[本地/远程工具适配通道]
        ExecutionSandbox[OS 进程 / FS 作用域 / 网络访问白名单]
        CredentialManager[凭证管理与人工作业授权]
    end

    Layer1 <--> Layer2
    Layer2 <--> Layer3
    Layer3 <--> Layer4
    Layer4 <--> Layer5
```

---

## 3. 核心机制精细化规范 (Key Mechanisms)

### 3.1 实体元模型 (Core Entities)
通用底座实体关系正规化：
```text
Project / Space (项目空间)
    └── Workflow (工作流实例)
          ├── Node (工作流节点)
          │     ├── Task (执行工单)
          │     │     └── Executor / Agent (具体执行单元)
          │     ├── Gate (准入/准出质量与合规门禁)
          │     └── Artifact (节点沉淀的第一公民产物)
          ├── Decision (人类审批与会签记录)
          └── Event (系统事实与注意力事件流)
```

### 3.2 双轨干预总线与调度权夺回 (Steer Bus & Control Reclaim)
* **硬制动（Hard Halt）的现实工程定义**：
  * **Halt ≠ 所有外部物理行为瞬间消失**；
  * **Halt = 系统在 100ms 内夺回调度权**。
  * 发出 `SIGINT` / 挂起信号，标记任务为 `cancel_requested`，立即阻止下游任何节点启动，本地子进程 P95 < 500ms 终止，未决外部请求隔离等待超时。
* **Agent Adapter 能力声明矩阵（Capability Matrix）**：
  由于多 Agent（Claude, Codex, OpenCode, Qoder, Agy, Pi）底层暴露接口各异，不假设所有 Agent 均支持思考间隙插话：
  ```python
  class AgentAdapterCapability:
      supports_interrupt: bool       # 是否支持软信号打断
      supports_queued_steer: bool    # 是否支持思考/工具调用间隙无感插话
      supports_step_boundary: bool   # 是否能精准截获 step boundary
      supports_resume: bool          # 是否支持断点上下文延续
  ```
  根据每个 Agent 的真实能力，系统自适应采用“间隙注入”、“重新 Prompt 恢复”或“任务结束后追加”策略。

### 3.3 注意力模型 (Attention Model)
每个 Node / Task 具备标准注意力状态：
* `requires_attention`: `bool`
* `attention_reason`: 简短的人类可读原因（如：“发现欧洲合规条款冲突，需法务拍板”）
* `attention_level`: `[NONE, INFO, WARNING, ACTION_REQUIRED, CRITICAL]`
* **前端呈现原则**：大屏优先聚合 `ACTION_REQUIRED` 与 `CRITICAL` 事项，实现一人轻量管控数十个并发 Agent。

### 3.4 耐久化状态存储演进策略 (Checkpoint Strategy)
* **V1（当前阶段）**：结构化 JSON Registry + 原子替换写（Atomic File Swap） + 追加式事件日志（Append-only Event Log）；
* **V2（单机成熟期）**：迁移至轻量级嵌入式 **SQLite**，支持图历史检索、状态分叉、重放（Replay）与快速回滚；
* **V3（远期分布式版）**：视多人与企业级上云诉求，再评估引入 PostgreSQL。

---

## 4. 演进路线图：从战壕里走出来的六阶段计划 (Phased Roadmap)

严格遵循“一次只解决当前最痛的一个工程问题”，拒绝好高骛远：

### Phase 0：先把当前系统变成“可靠内核” (Solidify the Reliable Kernel)
* **痛点**：多 Agent 冲突、僵尸 Pane、重启后任务状态丢失、历史 Workflow 相互污染。
* **任务**：
  1. 固化 **Anchor Self-Heal** 拓扑自愈（误删工位毫秒级补齐）；
  2. 修复 **`finalize_pending` 遗漏**，确保 Agent 退出与任务归档具有确定性闭环；
  3. 推进 **Candidate 分支自动化**，杜绝多节点实现相互污染；
  4. 强化 **Registry 耐久性** 与历史已完成 Workflow 的物理隔离。

### Phase 1：通用 Workflow / Node 元模型解耦 (Meta-Model Refactoring)
* **痛点**：业务逻辑与固定的 `requirements -> implementation -> test` 强绑定。
* **任务**：
  1. 建立通用的 `Project -> Workflow -> Node -> Task -> Executor` 抽象；
  2. 将现存软件工程流程收敛为第一个通用模板：`templates/software-development-v1.yaml`；
  3. 引入 **Artifact（产物）** 一级实体存储与关联。

### Phase 2：Durable Runtime 核心控制原语 (Kernel Control Primitives)
* **痛点**：出问题无法单步推进、无法暂停、回退逻辑硬编码。
* **任务**：
  1. 内核内部实现标准原子函数：
     * `pause_workflow()` / `resume_workflow()`
     * `retry_node()` / `rollback_to_checkpoint()`
     * `advance_one_step()` / `bypass_gate()`
  2. 将控制器改造成具备检查点能力的有限状态机。

### Phase 3：Agent Adapter 矩阵与双轨干预 (Adapter & Steer Bus)
* **痛点**：运行中无法优雅介入，不同 Agent 行为参差不齐。
* **任务**：
  1. 形式化定义 `AgentAdapter` 基类与能力声明字典（Claude/Codex/OpenCode 等适配器）；
  2. 实现以“夺回调度权”为核心的 `Hard Halt`；
  3. 在具备能力的 Adapter 上实现毫秒级 `Queued Steer`。

### Phase 4：事实采集、语义洞见与注意力大屏 (Telemetry & Attention)
* **痛点**：全量看日志看不过来，不知道谁卡住了，缺少商业/业务洞见。
* **任务**：
  1. 搭建确定性事实收集（Tool Call 规则直译）与 Agent 显式洞见交付（Milestone Protocol）双轨管道；
  2. 接入 **Attention Model**，将节点告警与待裁决事项聚合上浮；
  3. 完善交付物（Artifact）展示卡片。

### Phase 5：通用协同工作舱 (Universal Studio UI)
* **痛点**：终端交互门槛高，缺乏图形化编排与可视化审批。
* **任务**：
  1. 构建现代化三轨人机协同工作舱（注意力主屏 + 双轨介入坞 + 折叠物理抽屉）；
  2. 交付物沉浸式会签室（支持直接批注打回与一键放行）。

---

## 5. 工程级验收指标 (Engineering Success Metrics)

| 维度 | 指标项 | 目标要求 |
| :--- | :--- | :--- |
| **调度权控制** | Halt 信号接收并夺回调度权 | **P95 < 100ms** |
| | 本地受控子进程完全终止 | **P95 < 500ms** |
| | 制动后阻断任何下游节点启动 | **100%** |
| | 具备能力的 Agent 插话吸收成功率 | **P95 > 95%** |
| **鲁棒性与恢复** | Controller 异常重启后的活跃工作流恢复率 | **100%** |
| | 历史已完结工作流被误唤醒概率 | **0 次** |
| | 节点路由挑中不健康 Agent | **0 次** |
| | 并发锁（Reservation）冲突冲突逃逸 | **0 次** |
| **可观测性** | **“任何运行中的 Task，10 秒内清晰回答：谁在做？做什么？运行多久？卡在哪里？需不需要人？”** | **100% 覆盖** |
| **注意力信噪比** | 30 个并发 Agent 运行时，人类首屏仅需感知必要关注与决策项 | **首屏噪音降低 90%** |
