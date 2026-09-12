# 后台守护进程与服务运维指南 (Service Management)

> 本文档说明 Herdr 系统的 macOS LaunchAgent 后台守护进程配置、服务生命周期管理与日志排查手段。

---

## 1. 服务清单

系统由 4 个独立的后台服务共同协作组成：

| 服务标识 (Label) | 脚本路径 | 核心职责 |
| :--- | :--- | :--- |
| `com.user.herdr-controller` | `/Users/user/herdr/herdr-controller.py` | 任务状态监控、DAG 依赖推进、协调器事件分发。 |
| `com.user.herdr-notifier` | `/Users/user/herdr/herdr-notifier.py` | 任务与工作流完成时的 macOS 原生系统通知推送。 |
| `com.user.herdr-sentinel` | `/Users/user/herdr/herdr_sentinel.py` | 工位生命周期监控与空闲/僵死任务守护巡检。 |
| `com.user.herdr-factory-console` | `/Users/user/herdr/herdr-console.py` | 可视化 Web 控制台服务（默认端口 `8765`）。 |

---

## 2. 常用运维管理命令

### 2.1 查看服务运行状态
```bash
launchctl list | grep herdr
```
正常输出示例（第一列为系统 PID，第二列为最近退出码 `0`）：
```text
64501   0   com.user.herdr-controller
90611   0   com.user.herdr-notifier
90613   0   com.user.herdr-sentinel
68120   0   com.user.herdr-factory-console
```

### 2.2 重启服务（热更新代码后生效）
```bash
# 重启 Controller 调度控制器
launchctl kickstart -k gui/$(id -u)/com.user.herdr-controller

# 重启 Console 控制台
launchctl kickstart -k gui/$(id -u)/com.user.herdr-factory-console

# 重启 Notifier
launchctl kickstart -k gui/$(id -u)/com.user.herdr-notifier
```

### 2.3 停止与重新加载服务
```bash
# 停止 Controller
launchctl bootout gui/$(id -u)/com.user.herdr-controller

# 加载 Controller Plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.herdr-controller.plist
```

---

## 3. 日志文件与排查路径

所有服务输出的日志均统一存放在用户主目录下的 `.herdr-controller/logs/`：

| 日志文件 | 监控排查内容 |
| :--- | :--- |
| `~/.herdr-controller/logs/controller.out.log` | 任务轮询、状态转移、推进触发标准输出。 |
| `~/.herdr-controller/logs/controller.err.log` | 异常报错、崩溃追踪、网络通信异常。 |
| `~/.herdr-controller/logs/notifier.out.log` | 通知发送记录。 |
| `~/.herdr-controller/logs/console.out.log` | Web Console 请求日志。 |

### 实时日志跟踪命令：
```bash
tail -f ~/.herdr-controller/logs/controller.out.log
```
