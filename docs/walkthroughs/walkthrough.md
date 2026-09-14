# 仓库目录架构重构与最佳实践整理 Walkthrough

> **文档性质**：AI Agent 任务交付报告 (Walkthrough)  
> **归档时间**：2026-09-12  
> **任务目标**：整理 Herdr 仓库目录结构，符合业界最佳实践，消除根目录散落文件

---

## 1. 最终工程目录结构

```
herdr/
├── bin/                          # CLI 可执行命令行工具集
│   ├── herdr-factory             # 工作流与项目生命周期控制 CLI
│   ├── herdr-task                # Task 工单调度、现场分配与节点自愈 CLI
│   ├── herdr-preflight           # Agent 快速健康体检 CLI
│   └── herdr-deep-preflight      # Agent 沙盒深层探针 CLI
├── services/                     # 后台常驻守护进程与服务 (LaunchAgent 管理)
│   ├── herdr-controller.py       # DAG 依赖推进调度核心控制器
│   ├── herdr-sentinel.py         # Tab / Pane 存活巡检与僵死看门狗
│   ├── herdr-notifier.py         # macOS 原生通知派发服务
│   └── herdr-worker.py           # 独立 Task 工作区克隆与执行器
├── herdr/                        # 标准 Python 业务库包 (Core Library)
│   ├── __init__.py               # 包导出与向后兼容 sys.modules 映射
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
│   ├── context/                  # 系统演进上下文与历史转录文本
│   └── walkthroughs/             # 各类 Agent 生成的交付演进报告 (本目录)
├── pyproject.toml                # PEP 517/621 标准项目构建与依赖配置
├── README.md                     # 根目录主文档与导航指引
├── AGENTS.md                     # AI 上下文地图与快速索引
├── RULES.md                      # 开发作业规范与强制红线
├── CLAUDE.md                     # 开发入口、常用命令与环境坑点
└── .gitignore                    # 规范版本控制忽略规则
```

---

## 2. 核心调整与兼容性处理

1. **Python 包工程化 (`herdr/`)**
   - 核心模块被组织在标准包 `herdr` 下。
   - `herdr/__init__.py` 提供双向兼容机制：支持现代 `from herdr.workflow import ...`，同时自动向 `sys.modules` 注册 `herdr_workflow` 等别名，避免遗留调用发生导入中断。
   - 修复了模板查找路径，使得内置模板无论从包内调用还是外部调用均能稳定解析。

2. **可执行入口清晰化 (`bin/`)**
   - 将主控制脚本 `herdr-factory`、`herdr-task` 移至 `bin/`。
   - 新增 `herdr-preflight` 与 `herdr-deep-preflight` CLI 入口。
   - 全部 CLI 脚本添加 `#!/usr/bin/env python3` 并赋予可执行权限（`+x`）。

3. **后台守护进程规范化 (`services/`) 与 LaunchAgent 路径热切换**
   - 守护进程脚本统一归入 `services/`。
   - 更新系统级 LaunchAgent 描述文件：
     - `~/Library/LaunchAgents/com.user.herdr-controller.plist`
     - `~/Library/LaunchAgents/com.user.herdr-sentinel.plist`
     - `~/Library/LaunchAgents/com.user.herdr-notifier.plist`
   - 重启并热加载后台守护进程，保证 Controller、Sentinel、Notifier 正常运转。

4. **规范标准配置文件**
   - 建立 [pyproject.toml](file:///Users/user/HAFlow/pyproject.toml)，配置包元数据与 `pytest` 自动发现规则。
   - 规整 `.gitignore`，将历史临时备份文件统一归置在 `backups/` 目录下，根目录保持极致清爽。

---

## 3. 验证结果

| 测试项 | 执行命令 | 结果 |
| :--- | :--- | :--- |
| **自动化单元测试** | `pytest` | **16/16 全部通过** (0.09s) |
| **全局系统诊断** | `./bin/herdr-factory doctor` | **DOCTOR: PASS** (14项检查全部绿标) |
| **模板加载验证** | `./bin/herdr-factory templates` | 正常列出 `bidding`, `customer-service`, `software-development-v1` |
| **任务派发 CLI 验证** | `./bin/herdr-task --help` | 语法解析完整，无缺少依赖或路径错误 |
| **后台服务状态** | `launchctl list \| grep herdr` | 4 个服务全部正常运行 (退出码 0) |
| **静态编译检查** | `python3 -m compileall herdr/ services/ bin/ tests/` | 零语法错误、零导包异常 |
