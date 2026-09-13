# 通用人机协同底座 · 全链路端到端演练与集成报告 (Universal Runtime E2E Dogfooding)

## 1. 演练背景与目标

在完成《通用人机协同底座架构》五阶段演进（PR #11 - PR #15 全量合入主干）后，为防止“各模块单点可用但全链路集成脱节”的隐患，本任务针对五大阶段开展了**跨领域真实场景全链路端到端集成测试与自动化演练**。

演练以跨领域商业研报模板 [`business-research-v1.yaml`](file:///Users/user/herdr/workflow_templates/business-research-v1.yaml) 为核心载体，横跨五大架构层级执行闭环校验。

---

## 2. 全链路验证覆盖矩阵 (Verification Matrix)

| 阶段 / 架构层 | 核心能力项 | 验证手段与契约 | 演练结果 |
| :--- | :--- | :--- | :--- |
| **Phase 4: 动态元模型** | 跨领域 YAML 模板解析、无环拓扑校验、参数依赖检查 | `workflow.load_template`<br>`workflow.normalize_workflow`<br>`workflow.validate_workflow_dag` | ✅ 4 节点完整校验通过 |
| **Phase 1: 内核调度原语** | 运行中暂停、恢复、时间旅行快照与灾难恢复 | `api_kernel_pause`<br>`api_kernel_resume`<br>`api_kernel_checkpoint_create`<br>`api_kernel_checkpoint_restore` | ✅ 毫秒级状态切换与快照还原成功 |
| **Phase 4: 受控 MCP 网格** | 节点级能力按需挂载、操作权限门禁过滤 | `mcp.resolve_node_mcp`<br>`mcp.check_node_permissions` | ✅ 自动挂载 `web_search`/`data_extraction`，安全拦截非授权写操作 |
| **Phase 2: 双轨干预总线** | 非阻塞提示词排队注入、间歇出队消费、100ms 紧急制动 | `api_task_steer`<br>`steering.dispatch_pending_steer`<br>`api_task_halt` | ✅ 成功注入并消费，紧急制动瞬时置为 `interrupted` |
| **Phase 3: 4D 白盒投影** | ANSI 乱码终结、意图提取、动态路标计算、交付物收集 | `projection.extract_task_intent`<br>`projection.project_task`<br>`api_task_projection`<br>`api_workflow_projection` | ✅ 抹平机器噪音，产出高信噪比业务简报 |
| **Phase 5: 工作舱与会签室** | 注意力中枢聚合、成果批准放行、成果驳回回溯 | `api_task_signoff(action='approve')`<br>`api_task_signoff(action='reject')`<br>Attention Hub 4 态过滤计算 | ✅ 批准放行解除门禁；驳回优雅回滚至目标节点并固化人类反馈 |

---

## 3. 核心交付成果

1. **全链路端到端集成测试套件**：
   - 文件：[`tests/test_universal_substrate_e2e.py`](file:///Users/user/herdr/tests/test_universal_substrate_e2e.py)
   - 包含 4 大端到端集成测试场景：
     - `test_e2e_full_business_research_lifecycle_approve`：商业研报全生命周期（模板 -> 初始化 -> 挂起恢复 -> 插话制动 -> 白盒遥测 -> 会签批准放行）。
     - `test_e2e_business_research_reject_and_rollback_loop`：门禁节点会签驳回 (`action='reject'`)，验证工作流准确回退至上游节点，下游置为 `superseded`，人类评审反馈全量留痕。
     - `test_e2e_attention_hub_and_filter_telemetry`：验证 Attention Hub 智能聚合规则（待拍板 `decisions`、需关注 `attention`、活跃中 `active`）。
     - `test_e2e_checkpoint_lifecycle_and_restoration`：验证内核检查点快照存储、检索、状态人为破坏与时间旅行还原。

2. **独立可执行的实战演练工具 (Dogfooding CLI)**：
   - 文件：[`scripts/verify-universal-runtime-e2e.py`](file:///Users/user/herdr/scripts/verify-universal-runtime-e2e.py)
   - 支持开发者或 CI 随时一键执行全流程实战演练，打印彩色的高信噪比日志，自动在隔离环境验证五阶段全部核心原语。

3. **文档与归档索引治理**：
   - 更新 [`docs/walkthroughs/README.md`](file:///Users/user/herdr/docs/walkthroughs/README.md)，全量索引 Universal Substrate 的 7 份里程碑交付文档。

---

## 4. 验证命令与数据

```bash
# 1. 运行端到端独立演练脚本
python3 scripts/verify-universal-runtime-e2e.py

# 2. 运行新增的端到端集成测试
pytest -v tests/test_universal_substrate_e2e.py

# 3. 全仓全量回归测试 (321 项测试用例)
pytest
```

- **全仓回归结果**：**321 / 321 Passed (100%)**
- **执行耗时**：8.49s
- **依赖说明**：纯 Python 标准库与项目原生模块，无外部第三方依赖。
