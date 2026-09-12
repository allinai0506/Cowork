# Herdr Multi-Agent Workflow Platform

> **Tab = Workflow Node，Pane = Agent Workspace，Agent = Executor**  
> 基于 Herdr 的通用多 Agent 编排操作系统。

---

## 核心功能与特性

- **Tab = Workflow Node**：打破固定 6 阶段限制，任何业务流程（软件研发、标书制作、客服响应、市场调研等）均可通过声明式 DAG 模板定义。
- **Pane = Agent 工位**：动态分配独立的 Agent 执行现场，自带 Anchor Pane 锚点隔离与现场保留。
- **运行时自动自愈 (`ensure_node_runtime`)**：彻底解耦逻辑定义与 Tab/Pane ID。Tab 或 Anchor 误关后，任务派发时毫秒级自动修复重建。
- **DAG 依赖自动推进**：基于 Kahn 算法拓扑排序与依赖判定，支持多分支并发执行与依赖汇聚推进。
- **Node 级 Agent 策略**：精准配置特定节点的 Agent 偏好 (`preferred`)、固定执行者 (`fixed`) 或排除项 (`exclude`)。
- **深层健康探针 (Deep Preflight)**：沙盒实测验证 Agent 可用性，规避死锁与无效分发。
- **100% 向下兼容**：全面兼容既有项目、`--stage` 参数与历史工单。

---

## 快速导航

- 📖 **[完整使用指南 (UNIVERSAL_WORKFLOW_GUIDE.md)](file:///Users/user/herdr/docs/UNIVERSAL_WORKFLOW_GUIDE.md)**：包含核心模型、内置模板介绍、CLI 命令、自定义模板编写指南、自愈机制与 FAQ。
- 🏛️ **[架构设计全景文档 (HERDR_FACTORY_README.md)](file:///Users/user/herdr/HERDR_FACTORY_README.md)**：包含系统演进、各模块职责分工与底层实现细节。
- 🤝 **[多 Agent 协作交接记录 (herdr-multiagent-handoff.md)](file:///Users/user/herdr/herdr-multiagent-handoff.md)**：多 Agent 体系演进过程与各阶段决策。

---

## 快速开始

### 1. 查看可用模板
```bash
herdr-factory templates
```

### 2. 启动工作流
```bash
# 启动软件研发流程
herdr-factory run "实现用户权限控制系统" --template software-development-v1

# 启动标书制作流程
herdr-factory run "针对智慧城市项目的投标书制作" --template bidding

# 启动客户投诉处理流程
herdr-factory run "处理客户退款延迟投诉" --template customer-service
```

### 3. 查看状态与工单
```bash
# 查看工作流各节点状态
herdr-factory status <workflow_id>

# 查看节点 DAG 依赖与工位详情
herdr-task node-status --workflow-id <workflow_id>
```

### 4. 运行自动化测试
```bash
pytest -v tests/test_workflow_engine.py
```
