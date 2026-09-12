# 故障自愈与疑难排解 (Troubleshooting & FAQ)

> 本文档汇总 Herdr 系统在多 Agent 协同、工作流调度、Pane/Tab 状态异常等场景下的常见故障排查与自愈恢复方案。

---

## 1. 现场与工位问题

### 1.1 Tab 或 Anchor Pane 被误关闭怎么办？
- **现象**：在 Herdr 终端中不小心点了关闭 Tab，或者误关闭了名为 `"Anchor"` 的窗格。
- **排查**：执行 `herdr-task node-status --workflow-id <id>` 查看工位状态。
- **恢复**：系统已内置毫秒级自动自愈机制，下次派发任务或执行以下命令即可恢复：
  ```bash
  herdr-task ensure-runtime --workflow-id <workflow_id> --node <node_id>
  ```

### 1.2 Task 执行完成但 Pane 仍然卡在屏幕上？
- **机制说明**：为了方便排查和复盘，任务执行完成后默认会保留现场 Pane；只有在进入 `cleaned` 阶段或调用清理命令时才进行归档。
- **恢复/清理**：
  ```bash
  # 清理指定任务现场
  herdr-task cleanup <task_id>
  ```

---

## 2. 调度与推进问题

### 2.1 任务执行成功了，为什么后续节点没有自动推进？
- **排查 1（验收准则）**：检查前置任务是否真正达到完成状态（`status=cleaned` 或 `status=completed`）。未达标的任务不会触发下游推进。
- **排查 2（汇聚节点）**：如果下游节点有多个依赖（如标书流程中的 `strategy` 同时依赖 `scoring_extract` 和 `history_search`），必须等待**所有**依赖分支全部完成才会激活下游。
- **排查 3（Controller 服务日志）**：查看控制器日志：
  ```bash
  tail -n 50 ~/.herdr-controller/logs/controller.out.log
  ```

### 2.2 启动 Workflow 提示 `No READY Agent found`？
- **原因**：当前机器上所有的 Agent 均未能通过 Deep Preflight 探针测试（例如 API Token 失效或本地未安装对应 CLI）。
- **排查**：运行 `herdr-factory doctor` 或运行：
  ```bash
  herdr-deep-preflight --deep
  ```
  根据具体的报错提示配置相应 Agent 的环境变量或执行登录。

---

## 3. 并发锁与死锁问题

### 3.1 提示 `Reservation timeout` 或 Agent 无法获取？
- **排查**：检查 `~/.herdr-controller/router-reservations.json` 是否存在过期死锁记录。
- **恢复**：系统在每次路由时会自动根据 60 秒 TTL 剔除失效预占，通常等待 1 分钟后重试即可；或手动重置预占文件：
  ```bash
  echo '{"reservations": {}}' > ~/.herdr-controller/router-reservations.json
  ```
