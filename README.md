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

## 📖 文档体系导航

Herdr 遵循业界最严格的文档分类标准，所有文档严禁散落存放在 `docs` 根目录，全部按职责严格分门别类：

- **架构设计 (`docs/architecture/`)**
  - [系统全局架构设计](file:///Users/user/herdr/docs/architecture/architecture-overview.md)：系统定位、分层模型与核心子系统职责。
  - [Tab=Workflow Node 架构模型](file:///Users/user/herdr/docs/architecture/tab-node-model.md)：空间现场模型、Anchor 母体机制与逻辑运行时解耦。
- **实操指南 (`docs/guides/`)**
  - [通用工作流使用指南](file:///Users/user/herdr/docs/guides/universal-workflow-guide.md)：从模板启动、任务派发、状态监控到自愈机制的完整手册。
  - [模板编写实战指南](file:///Users/user/herdr/docs/guides/template-authoring-guide.md)：自定义 DAG 工作流模板从 0 到 1 编写与调试。
- **产品与数据规范 (`docs/product-specs/`)**
  - [工作流模板 Schema 规范](file:///Users/user/herdr/docs/product-specs/workflow-template-schema.md)：YAML/JSON 字段契约、数据类型与 DAG 校验边界。
  - [Agent 策略与路由规范](file:///Users/user/herdr/docs/product-specs/agent-policy-spec.md)：Node 级 Agent 策略、健康准入与预占锁。
- **运维与排障手册 (`docs/operations/`)**
  - [后台守护进程运维手册](file:///Users/user/herdr/docs/operations/service-management.md)：LaunchAgent 管理、重启命令与日志追踪。
  - [Deep Preflight 探针手册](file:///Users/user/herdr/docs/operations/deep-preflight-playbook.md)：各主流 Agent 沙盒试跑机制与探针适配。
  - [故障自愈与疑难排解 FAQ](file:///Users/user/herdr/docs/operations/troubleshooting-faq.md)：现场误关恢复、调度汇聚阻断与死锁恢复。
- **命令与参考资料 (`docs/references/`)**
  - [CLI 全量命令参考手册](file:///Users/user/herdr/docs/references/cli-reference.md)：`herdr-factory` 与 `herdr-task` 所有子命令与选项。
- **演进与交接记录 (`docs/handoffs/`)**
  - [多 Agent 研发系统交接记录](file:///Users/user/herdr/docs/handoffs/multiagent-handoff.md)：多 Agent 体系演进过程与历史决策。

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
