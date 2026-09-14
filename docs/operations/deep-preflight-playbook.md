# Deep Preflight 探针手册 (Deep Preflight Playbook)

> 本文档说明 Herdr Deep Preflight 机制的原理、各 Agent 探针适配逻辑与健康诊断方法。

---

## 1. 为什么需要 Deep Preflight？

在过去实验中，单纯通过 `which <agent>` 检查二进制是否存在是远远不够的：
- Agent 依赖的远程 API Key 可能已过期；
- Agent CLI 可能陷入交互式登录授权死循环；
- 某些 Agent 在后台 LaunchAgent 环境下因找不到 Login Shell 环境变量而报错。

**Deep Preflight 机制**通过沙盒执行极小微任务（如让 Agent 返回 `READY` 并立即退出），确保分发任务前该 Agent 确实处于健康可响应状态。

---

## 2. 支持的 Agent 与探针适配机制

系统在 `herdr/deep_preflight.py`（CLI: `herdr-deep-preflight`）中为各大主流 Agent 提供了专门的无副作用探针：

| Agent | 探针适配策略 | 超时时间 | 验收准则 |
| :--- | :--- | :--- | :--- |
| `claude` | 无头单回合模式 (`claude -p "output READY..."`) | 25s | 进程返回 0 且包含 `READY` |
| `codex` | 静默命令模式 (`codex exec "echo READY..."`) | 25s | 进程返回 0 且包含 `READY` |
| `opencode` | 极简执行 (`opencode run "echo READY..."`) | 25s | 进程返回 0 且包含 `READY` |
| `qodercli` | 脚本模式 (`qodercli -p "print READY..."`) | 25s | 进程返回 0 且包含 `READY` |
| `agy` | CLI 简短交互 (`agy -p "output READY..."`) | 25s | 进程返回 0 且包含 `READY` |
| `pi` | 轻量指令模式 | 20s | 进程返回 0 且包含 `READY` |

---

## 3. 手动执行与排障

### 3.1 运行全量沙盒检测
```bash
herdr-deep-preflight --deep
# 或完整路径：
python3 /Users/user/HAFlow/bin/herdr-deep-preflight --deep
```

### 3.2 针对特定项目排查
```bash
herdr-deep-preflight --project-id xiyu-bid-poc --deep
```

### 3.3 输出 JSON 诊断报告
```bash
herdr-deep-preflight --deep --json | jq .
```
