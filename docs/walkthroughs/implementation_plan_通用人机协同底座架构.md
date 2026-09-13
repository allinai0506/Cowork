# 通用人机对等协同底座架构与开发计划 (Universal Human-Agent Collaborative Substrate)

## 1. 架构定位与愿景

本框架旨在打造一个**面向任意领域（Domain-Agnostic）的通用人机协同底座与操作系统内核**。
它不局限于软件开发，而是能够将任何复杂的知识型、分析型与流程型工作（如商业投研、策划方案、合同法务、业务流程运转等）抽象为可配置的协同图谱（Collaborative Graph）。

系统核心理念：
1. **人机对等（Peer-to-Peer）**：无科层职级，人类与 Agent 组成动态能力蜂窝，互相协作、对等质询。
2. **白盒高信噪比（High-SNR Transparency）**：剔除机器乱码、冗长思考流与细碎 Diff，提炼出人类易读的“意图 + 路标 + 影响面 + 决策点”。
3. **实时打断与纠偏（In-Flight Steerability）**：在 Agent 工作循环间隙实现优雅插话与紧急制动。
4. **配置驱动与受控生态（Declarative Config & Sandboxed MCP）**：通过纯配置（YAML/JSON）动态装配任何工作流、执行者模型与权限工具包。

---

## 2. 业内主流 Agent 产品与框架调研与技术对标

通过对业内最前沿的 Agent 产品和框架（Devin、LangGraph、Temporal、Claude Code/Cursor）的机制剖析，提炼出可借鉴的最佳实践：

### 2.1 人机介入与打断机制 (Human-in-the-Loop & Steerability)
* **LangGraph (LangChain)**:
  * *Static/Dynamic Breakpoints*: 在节点前（`interrupt_before`）或节点后（`interrupt_after`）声明中断，让图挂起等待人类。
  * *Time-Travel & State Forking*: 每次状态迁移均持久化 Checkpoint，支持人类时间旅行（回滚到任意历史版本，修改状态值后分支重跑）。
  * *Update State*: 人类通过 `update_state()` 直接改写图的上下文，无缝注入外部决策。
* **Devin (Cognition AI)**:
  * *Chat as Control Stream*: 用户输入不是简单的终端打字，而是直接作为高优先级事件推入 Agent 的待办序列；当检测到人类打字，当前未完成动作会优雅收口并向人类汇报。
* **Claude Code (Anthropic)**:
  * *Signal Handled Re-prompt*: 终端按下 `Ctrl+C` 触发软中断，终止当前 API 生成或子进程，保留全部历史会话，立即可接收人类纠偏指令。
* **Temporal**:
  * *Signals & Queries*: 通过 Signal 实现异步非阻塞的外部意图注入（如“人类点击了暂停/打回”），通过 Query 实现无副作用的白盒状态探测。

### 2.2 信息提炼与去噪 (Information Distillation)
* **Devin 的三轨展示架构**:
  * 第一轨（主视窗）：**结构化进展卡片**（Checklist、阶段目标、关键结论）。
  * 第二轨（交互视窗）：**人类对话与方案述职**。
  * 第三轨（抽屉视窗）：**底层物理现场**（终端、浏览器、文件查看器）。平时折叠收起，需要排障时一键展开。
* **Cursor / Windsurf**:
  * 将复杂的底层文件改动和终端执行压缩为单行 Badge（如 `Read 4 files`, `Ran pytest (passed)`），点击才展开详情。

---

## 3. 通用底座系统全景架构

```mermaid
graph TD
    subgraph UI_Layer [1. 通用协同视窗 (Universal Human-Agent Studio)]
        HighSNRView[高信噪比白盒大屏: 意图 / 路标 / 关键洞察]
        InterventionDock[人类干预与控制台: 暂停 / 单步 / 打回 / 插话]
        HandoffModal[成果交付会签室: 结构化产物审批]
        DeepDrawer[底层物理抽屉: 可选展开终端/原始数据]
    end

    subgraph Projection_Layer [2. 语义投影与提炼引擎 (Projection Engine)]
        EventFilter[Telemetry 过滤器: 过滤机器乱码与思考絮叨]
        Synthesizer[摘要生成器: 状态流转化为人类高管简报]
        DiffCondenser[产物宏观影响分析器]
    end

    subgraph Kernel_Layer [3. 调度与编排内核 (Controller / Kernel)]
        DAGScheduler[拓扑依赖推进调度器]
        StateManager[(Durable State DB: 检查点与时间旅行快照)]
        GateVerifier[通用质量与合规门禁]
        RollbackManager[状态回溯与 Fix-Loop 引擎]
    end

    subgraph Runtime_Layer [4. 运行时与介入总线 (Worker & Intervention Mesh)]
        WorkerShim[通用 Worker 代理垫片: 拦截循环间隙]
        SignalHandler[软干预 / 紧急制动调度器]
        MCPHost[MCP 技能与数据通道容器]
        SandboxIsolation[权限受限沙盒]
    end

    UI_Layer <--> Projection_Layer
    Projection_Layer <--> Kernel_Layer
    Kernel_Layer <--> Runtime_Layer
```

---

## 4. 底座核心元模型设计 (Core Abstractions)

### 4.1 节点通用契约 (Node Contract)
不再有写死的“代码实现”或“单元测试”，统一抽象为通用节点规格：
```yaml
node_id: "competitive_analysis"
label: "竞品动态与财报横向比对"
purpose: "提炼出三家主要竞品的毛利异动原因"
worker_policy:
  model: "claude-3-7-sonnet"
  capabilities: ["web_research", "financial_excel_calc"]
  permissions: ["read_only_public_data"]
inputs:
  - ref: "context.user_goal"
  - ref: "nodes.market_scope.outputs"
gate:
  type: "hybrid" # 自动判定 + 人工会签
  auto_criteria: "outputs.table_rows >= 3"
  requires_human_approval: true # 人工终审卡点
retry_target: "nodes.market_scope" # 门禁失败回退目标
```

### 4.2 提炼事件规范 (High-SNR Telemetry Schema)
Worker 向外广播的标准事件流：
```json
{
  "timestamp": 1773478900,
  "node_id": "competitive_analysis",
  "phase": "running",
  "intent": "正在交叉校验竞品 A 在财报附注中披露的海外物流折旧率",
  "milestones": [
    {"title": "提取 2025Q3 财报核心指标", "status": "done"},
    {"title": "计算各业务线息税前利润", "status": "active"},
    {"title": "输出异动对比图表", "status": "pending"}
  ],
  "impact_summary": "发现物流成本口径调整，预计影响此前分析结论约 4.5%",
  "requires_attention": false
}
```

---

## 5. 五阶段开发与演进计划 (Phased Roadmap)

### 阶段一：内核调度解耦与控制元语暴露 (Kernel Control Primitives)
* **目标**：将后台 Controller 从“自主闷头跑”改造成“支持外部全量控制”的开放内核。
* **关键工作**：
  1. 梳理并暴露标准的内核控制接口（REST/Socket）：
     - `POST /api/kernel/pause`（全局或单节点挂起）
     - `POST /api/kernel/resume`（恢复自动推进）
     - `POST /api/kernel/step`（单步触发就绪节点）
     - `POST /api/kernel/rollback`（指定回退到任意历史节点并清除下游状态）
     - `POST /api/kernel/force_pass`（人工强行放行门禁）
  2. 实现图运行时的检查点持久化（Checkpoint Snapshot），确保任何时刻均可暂停、回退。

### 阶段二：Worker 代理垫片与实时打断机制 (Intervention & Steering Mesh)
* **目标**：实现对任何 Agent（无论 Codex 还是 Claude）在运行间隙的即时打断与插话。
* **关键工作**：
  1. 开发 **Worker Runtime Shim**：包裹在终端与 CLI Agent 外部的轻量拦截器。
  2. 实现 **插话队列（In-Flight Steer Queue）**：人类在界面发送的一句话指令，在 Agent 当前 Turn 结束、Next Turn 开始的毫秒级缝隙注入为高优先级提示词。
  3. 实现 **紧急制动（Halt）**：向底层 TTY 发送受控软中断（SIGINT），捕获进程现场并转为等待人类输入状态。

### 阶段三：语义提炼引擎与白盒数据流 (Projection Engine)
* **目标**：抹去终端原始乱码与机器输出，提炼人类友好的四维简报。
* **关键工作**：
  1. 制定通用结构化 Telemetry 协议（意图、路标、影响摘要、求助）。
  2. 开发轻量级提炼管道（Distillation Pipeline）：将 Agent 的底层输出、Tool 调用实时映射提炼为高信噪比卡片数据。
  3. 支持产物（Artifact）第一公民：文档、数据表、分析结论作为独立卡片实时呈现。

### 阶段四：通用配置驱动与受控 MCP 生态容器 (Dynamic Configuration & Capabilities)
* **目标**：彻底告别硬编码业务逻辑，任何协同流全靠配置生成。
* **关键工作**：
  1. 完善通用 DAG 模板协议（支持任意领域的节点、前置依赖、门禁与回退规则）。
  2. 实现 MCP 插件池与按需挂载：在节点启动时按声明挂载对应的工具包（查询类、生成类、外部通信类）。
  3. 建立工位安全与权限隔离机制（只读/读写/必须人工审批）。

### 阶段五：通用人机对等协同工作舱 (Universal Studio UI)
* **目标**：打造极具质感、白盒化、支持随时干扰的人机共事界面。
* **关键工作**：
  1. 重构前端控制台为“三轨工作舱”：
     - 主视觉区：**提炼后的白盒大屏**（意图跟踪、动态路标、关键洞见）；
     - 操作底座区：**打断与单步调试台**（随时插话、暂停、强制推进、打回）；
     - 抽屉折叠区：**底层物理现场**（按需窥探原生终端与原始输出）。
  2. 交付会签室：当触发人类门禁时，弹窗沉浸式展示结构化交付物，提供一键会签或批注打回功能。

---

## 6. 验证与演进指标 (Success Metrics)

1. **可控性指标**：人类在界面点击“暂停”或“插话”后，系统在 2 秒内优雅响应并使 Agent 接收新指令的成功率达到 100%。
2. **信噪比指标**：人类无需打开任何底层终端或日志窗口，仅凭前端大屏即可知晓 90% 以上的进展与关键取舍。
3. **通用性指标**：在不修改一行底座代码的前提下，仅通过两份不同的 YAML 配置文件，能够分别成功运行“软件开发迭代流”与“跨部门商业调研分析流”。
