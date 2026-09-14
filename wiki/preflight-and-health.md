# 体检与沙盒健康探针 (preflight-and-health.md)

> **公司：上海共事智能科技有限公司**  
> **品牌：共事**  
> **产品：HAFlow**  
> **一句话：让人和多个 AI Agent 一起把事情做完**  
> *Human + Agent, in Flow*  
> **轻量探活、Deep Preflight 沙盒深探与 Token/Auth 模式识别**  
> 关联索引: [[index]] | [[system-overview]] | [[agent-routing-and-pools]] | [[architecture]]

---

## 1. 双层体检架构定位

为了避免向无法连通、凭证过期或配额耗尽的 Agent 盲目派发任务造成流水线死锁，HAFlow 建立了分层的准入体检机制：

```mermaid
graph LR
    subgraph L1 [1. 轻量静态体检 (herdr-preflight)]
        BinCheck[CLI 二进制检测] --> VerCheck[--version 版本探测]
        VerCheck --> AuthHintCheck[本地配置文件存在性]
    end

    subgraph L2 [2. 沙盒动态深探 (herdr-deep-preflight)]
        SandboxInit[创建临时隔离沙盒] --> NonDestructProbe[执行极简无副作用指令]
        NonDestructProbe --> PatternMatch[正则表达式分类器]
        PatternMatch --> Classify[判定: READY / TOKEN_LIMIT / AUTH_EXPIRED]
    end

    L1 --> L2
    L2 --> WriteHealthy[回写 healthy_agents 供 Router 消费]
```

Evidence:
- `herdr/preflight.py`
- `herdr/deep_preflight.py`
- `docs/operations/deep-preflight-playbook.md`

---

## 2. 轻量静态体检 (`herdr-preflight`)

`FACT` 用于日常快速巡检，耗时毫秒级，不消耗任何模型 Token：
1. **二进制可执行探测**: 检查 `opencode`, `codex`, `claude`, `qodercn`, `agy`, `pi` 是否可解析。解析顺序：`shutil.which`（进程 PATH）→ 常见安装目录兜底（`~/.local/bin`、`~/.volta/bin`、`~/.qoder-cn/entry`、homebrew）→ 登录 shell `command -v` 终极兜底。LaunchAgent 服务的精简 PATH 不再导致已装 CLI 被误判为"未安装"。
2. **版本号探测**: 带 8 秒超时的 `--version` 探活，防止二进制由于系统动态链接库缺失而僵死。
3. **本地凭证提示 (`AUTH_HINTS`)**:
   - Codex: `~/.codex/auth.json`
   - Claude: `~/.claude.json`
   - Pi: `~/.pi/agent/auth.json`
   - OpenCode: `~/.config/opencode`
   - QoderCLI: `~/.qoder-cn`

Evidence:
- `herdr/agent_binary.py:AGENT_BINARIES`（agent id -> CLI 映射单一事实来源）
- `herdr/agent_binary.py:resolve_binary` / `resolve_agent_binary`
- `herdr/preflight.py:AUTH_HINTS`
- `herdr/preflight.py#probe_version`

---

## 3. 深层沙盒动态体检 (`herdr-deep-preflight`)

### 3.1 探针无副作用安全契约 (Non-Destructive Safety)
> [!IMPORTANT]
> **探针安全红线**  
> `deep-preflight` 探活时，严禁让 Agent 生成大批量代码或触发高昂 Token 消耗。  
> 探针必须在临时沙盒中通过极简空指令（如 `"echo READY"` 或最小 ping 请求）验证连通性。

### 3.2 异常分类与正则模式库
`FACT` 探针通过对 CLI 输出进行保守的正则特征匹配，精确区分故障原因：

| 故障类别 | 正则匹配模式 (Patterns) | 判定结果 |
| :--- | :--- | :--- |
| **Token / 配额耗尽** | `token.*(exhaust\|limit\|quota)`<br/>`quota.*exhaust`<br/>`rate.?limit` | `TOKEN_EXHAUSTED` |
| **认证失效 / 未登录** | `not logged in`<br/>`authentication required`<br/>`unauthorized`<br/>`invalid api key` | `AUTH_REQUIRED` |
| **工作区未授权信任** | `do you trust`<br/>`workspace trust` | `TRUST_REQUIRED` |
| **正常就绪** | 正常返回且未匹配任何阻断规则 | `READY` |

### 3.3 Claude 工作区信任自动铺路
针对 Claude Code 常见的 `"do you trust this folder"` 阻塞对话框，系统在任务启动前（`services/herdr-worker.py#ensure_claude_workspace_trust`）自动向 `~/.claude.json` 注入 `hasTrustDialogAccepted = True`，从根源消除交互式卡死。

Evidence:
- `herdr/deep_preflight.py:TOKEN_PATTERNS`
- `herdr/deep_preflight.py:AUTH_PATTERNS`
- `services/herdr-worker.py#ensure_claude_workspace_trust`
- `RULES.md:探针无副作用安全`
