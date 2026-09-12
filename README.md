# Herdr Multi-Agent Workflow Platform

> **Tab = Workflow Node，Pane = Agent Workspace，Agent = Executor**  
> 基于空间现场模型的通用多 Agent 工作流编排操作系统。

---

## 📌 项目背景与定位 (Project Background)

在大语言模型驱动的智能体研发体系中，多 Agent 协同正迅速从早期单线、固定的 6 阶段研发演进为复杂、多变的通用业务流程（如招投标标书生成、多角色客服仲裁、企业级自动化调研等）。

**Herdr Multi-Agent Workflow Platform** 旨在为多 Agent 协同提供一套生产级的“操作系统底座”：
- **物理现场与逻辑流程统一**：将终端窗口/标签页作为工作流节点（Tab = Workflow Node），将窗格作为独立 Agent 工位（Pane = Agent Workspace）。
- **流程解耦与自由拓扑**：打破固定流程枷锁，支持任意有向无环图（DAG）工作流模板定义。
- **自愈与高可用保障**：将易失的运行时现场与不可变的逻辑定义分离，实现误关自动自愈、探针沙盒健康体检与并发防死锁调度。

---

## 🚀 核心功能与特性 (Core Features)

1. **Tab = Workflow Node（声明式 DAG 工作流）**  
   任何业务流程（研发、标书、客诉等）均通过声明式 YAML/JSON 模板定义，支持分支并发与条件汇聚。
2. **Pane = Agent Workspace（工位现场保留与 CoW 隔离）**  
   动态分配独立的 Agent 执行现场，任务执行在独立 Git Copy-on-Write (CoW) 克隆中，自带 Anchor 锚点现场保护。
3. **运行时自动自愈 (`ensure_node_runtime`)**  
   彻底解耦逻辑定义与 Tab/Pane ID。Tab 或 Anchor 误关后，任务派发时毫秒级自动修复重建，永不中断流程。
4. **DAG 依赖自动推进**  
   基于 Kahn 算法拓扑排序与依赖判定，前置任务完成后由 Controller 守护进程自动推进后续就绪节点。
5. **Node 级 Agent 策略与负载路由**  
   精准配置节点的 Agent 偏好 (`preferred`)、固定执行者 (`fixed`) 或排除项 (`exclude`)，配合预占锁分摊并发负载。
6. **深层健康探针 (Deep Preflight)**  
   为主流 Agent（Claude、Codex、OpenCode、Qoder、Agy、Pi 等）提供无副作用的沙盒实测验证，阻断死锁与无效分发。
7. **100% 向下兼容**  
   双向归一化引擎全面兼容既有项目、`--stage` 参数与历史工单，平滑升级无断层。

---

## 🛠️ 技术栈 (Technology Stack)

- **核心语言与环境**：Python 3.9+（经过 Python 3.13 严格验证）、PEP 517/621 规范。
- **配置与编排契约**：PyYAML、JSON Schema 契约、Kahn 算法有向无环图拓扑排序。
- **常驻后台系统**：macOS LaunchAgent 集群架构（Controller 核心调度、Sentinel 看门狗、Notifier 原生通知、Web Console 控制台）。
- **底座通信与现场控制**：Herdr 多工位终端管理、Unix Domain Socket (`~/.config/herdr/herdr.sock`) 跨进程 IPC。
- **版本控制与沙盒隔离**：Git CoW 物理克隆隔离、基于 Git Tree 校验的 Task Baseline 差异比对。
- **测试框架**：Pytest 自动化回归测试套件。

---

## 🏗️ 仓库目录架构规范 (Directory Layout)

Herdr 严格遵循现代分布式系统与 Python 开源工程最佳实践，严禁在根目录堆放平铺代码：

```
herdr/
├── bin/                          # CLI 可执行命令行工具集 (PATH 入口)
│   ├── herdr-factory             # 工作流与项目生命周期控制 CLI
│   ├── herdr-task                # Task 工单调度、现场分配与节点自愈 CLI
│   ├── herdr-preflight           # Agent 快速健康体检 CLI
│   └── herdr-deep-preflight      # Agent 沙盒深层探针 CLI
├── services/                     # 后台守护进程与常驻服务 (LaunchAgent 管理)
│   ├── herdr-controller.py       # DAG 依赖推进与协调核心控制器
│   ├── herdr-sentinel.py         # Tab / Pane 存活巡检与僵死看门狗
│   ├── herdr-notifier.py         # macOS 原生通知派发服务
│   └── herdr-worker.py           # 独立 Task 工作区克隆与执行器
├── herdr/                        # 标准 Python 核心业务库包 (Core Library)
│   ├── __init__.py               # 包统一导出与向下兼容别名映射
│   ├── workflow.py               # 工作流定义解析、Kahn 算法 DAG 校验与就绪节点计算
│   ├── agent_router.py           # Node 级 Agent 策略匹配、探活准入与预占锁路由
│   ├── pane_pool.py              # 空间现场 Pane 槽位分配与状态绑定
│   ├── projects.py               # 多项目元数据管理、工作流注册与运行时自愈探活
│   ├── topology.py               # Node/Stage 拓扑现场动态自愈与 Anchor 重建
│   ├── preflight.py              # Agent 基础状态检测
│   └── deep_preflight.py         # Agent 深层沙盒探针
├── scripts/                      # 运维、安装、迁移与系统辅助脚本
│   └── herdr-topology-selfheal-install.sh
├── workflow_templates/           # 声明式工作流 DAG 模板定义 (YAML)
│   ├── bidding.yaml
│   ├── customer-service.yaml
│   └── software-development-v1.yaml
├── tests/                        # 自动化测试套件
│   ├── __init__.py
│   └── test_workflow_engine.py
├── docs/                         # 分级结构化系统文档体系
│   ├── architecture/             # 架构设计与空间现场模型
│   ├── guides/                   # 通用工作流与模板编写指南
│   ├── product-specs/            # Schema 规范与 Agent 策略标准
│   ├── operations/               # 守护进程运维手册与排障 FAQ
│   ├── references/               # CLI 参考手册与归档历史说明
│   └── context/                  # 系统演进上下文与历史转录文本
├── pyproject.toml                # PEP 517/621 标准项目构建与依赖配置
├── README.md                     # 根目录主文档与导航指引
├── AGENTS.md                     # AI 上下文地图与快速索引
├── RULES.md                      # 开发作业规范与强制红线
├── CLAUDE.md                     # 开发入口、常用命令与环境坑点
└── .gitignore                    # 规范版本控制忽略规则
```

---

## 📖 核心文档导航

- **AI 导航地图**: [AGENTS.md](file:///Users/user/herdr/AGENTS.md) — 紧凑型上下文指针。
- **开发作业红线**: [RULES.md](file:///Users/user/herdr/RULES.md) — ECC 4 阶段流程与系统禁忌。
- **操作入口避坑**: [CLAUDE.md](file:///Users/user/herdr/CLAUDE.md) — 命令字典与常见坑点。
- **架构设计**: [系统全局架构设计](file:///Users/user/herdr/docs/architecture/architecture-overview.md) 与 [Tab=Workflow Node 架构模型](file:///Users/user/herdr/docs/architecture/tab-node-model.md)。
- **实操手册**: [通用工作流使用指南](file:///Users/user/herdr/docs/guides/universal-workflow-guide.md) 与 [模板编写实战指南](file:///Users/user/herdr/docs/guides/template-authoring-guide.md)。
- **运维排障**: [后台服务运维手册](file:///Users/user/herdr/docs/operations/service-management.md) 与 [故障自愈 FAQ](file:///Users/user/herdr/docs/operations/troubleshooting-faq.md)。

---

## ⚡ 快速开始 (Quick Start)

### 1. 配置命令行 PATH（推荐）
```bash
export PATH="$HOME/herdr/bin:$PATH"
```

### 2. 全局环境自检
```bash
herdr-factory doctor
```

### 3. 查看可用模板
```bash
herdr-factory templates
```

### 4. 启动工作流
```bash
# 启动软件研发流程
herdr-factory run "实现用户权限控制系统" --template software-development-v1

# 启动标书制作流程
herdr-factory run "针对智慧城市项目的投标书制作" --template bidding
```

### 5. 查看状态与工单
```bash
# 查看工作流各节点状态
herdr-factory status <workflow_id>

# 查看节点 DAG 依赖与工位详情
herdr-task node-status --workflow-id <workflow_id>
```

### 6. 运行自动化测试
```bash
pytest -v tests/test_workflow_engine.py
```
