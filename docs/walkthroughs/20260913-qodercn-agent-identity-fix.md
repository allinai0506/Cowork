# 2026-09-13 Qoder/QoderCN Agent 身份错装修复（第三次修复为何才生效）

## 用户报告

> agent 中的 qoder 不对，正确的是 qodercn，已经改过两次了，为什么还没好？这是两个不同的 Agent。

## 结论（TL;DR）

前两次修复（7d6dc5a、00d52ba）都只改了 repo 内的**探测层**映射，而真正的病灶在
repo 之外：herdr 工具把 qodercli 的会话上报钩子安装进了**国际版 Qoder** 的产品目录
`~/.qoder`，实际运行的 Qoder CN CLI（配置目录 `~/.qoder-cn`）根本读不到，导致
qodercli agent 从上线起就没有 `agent_session` 上报；同时 herdr 检测 manifest 把
国际版 `qoder` 列为 CN agent 的别名，两个不同产品被混为一体。本次把钩子迁入
`~/.qoder-cn`、从 `~/.qoder` 摘除、并用本地 manifest 覆盖摘掉 `qoder` 别名，
端到端验证 `agent_session` 首次上报成功。

## 证据链（排查顺序）

1. **repo 内无裸 `qoder` 引用**：`rg '\bqoder\b'` 仅命中文案；`herdr/agent_binary.py:23`
   `AGENT_BINARIES["qodercli"]="qodercn"`，tests/test_agent_binary_resolution.py、
   tests/test_console_agent_roster.py 均锁定该映射。部署副本 `~/.herdr-console` 与
   repo diff 一致 → 排除"改了没部署"。
2. **运行时状态一致**：`~/.herdr-controller/{workflows,agent-pools,tasks}.json`
   全部是 `qodercli`；867 处裸 `qoder` token 全部位于 `clones/*/.qoder/`（agent
   工作区里国际版产品自己的数据），非系统记录。
3. **实际进程**：`ps aux` 显示 4 个 agent 跑的是
   `/Users/user/.local/bin/qoderclicn --dangerously-skip-permissions`（CN CLI，
   `ps eww` 确认无 QODERCN_CONFIG_DIR 覆盖 → 配置目录 `~/.qoder-cn`）。
4. **两个产品并存**：`~/.qoder`（国际版，CLI `~/.qoder/bin/qoder`）与
   `~/.qoder-cn`（CN，官方 dispatcher `~/.qoder-cn/entry/qodercn`，内部 exec
   `qoderclicn`）。用户所说"两个不同的 Agent"即此。
5. **herdr 工具层（病灶）**：
   - `herdr agent start --help`：kind 列表仅 `qodercli`，无 qoder/qodercn；
   - `~/.local/state/herdr/agent-detection/remote/qodercli.toml`：
     `aliases = ["qoderclicn", "qoder", "qodercn"]`（国际版被并为别名）；
   - herdr 二进制 strings 仅含 `.qoder` 一个路径 token → 集成安装器硬编码
     国际版目录；
   - 实锤：`~/.qoder/settings.json` 有 `hooks.SessionStart → herdr-agent-state.sh`，
     `~/.qoder/hooks/herdr-agent-state.sh` 存在，而 `~/.qoder-cn` 内无任何 herdr
     痕迹；`herdr agent list` 中 qodercli 全部缺 `agent_session`。
6. **CN CLI 能力确认**：qoderclicn 二进制 59 处 `SessionStart`，hooks 机制完整。

## 修复内容（全部可回滚）

| # | 动作 | 路径 |
|---|------|------|
| 1 | 钩子脚本迁入 CN 产品目录 | `~/.qoder-cn/hooks/herdr-agent-state.sh`（0755） |
| 2 | CN settings.json 增加 SessionStart 钩子 | `~/.qoder-cn/settings.json`（备份 `.bak.herdr-fix-20260913-101041`） |
| 3 | 国际版 settings.json 摘除 herdr 钩子（保留用户自己的 entry-gate 钩子） | `~/.qoder/settings.json`（同上备份） |
| 4 | 删除装错位置的钩子脚本 | `~/.qoder/hooks/herdr-agent-state.sh` |
| 5 | 检测别名本地覆盖：`aliases=["qoderclicn","qodercn"]`（去掉国际版 `qoder`） | `~/.config/herdr/agent-detection/qodercli.toml` + `herdr server reload-agent-manifests` |
| 6 | 验证后清理 | 临时 tab/pane（w6:t8/w6:p1Z、w6:t9/w6:p10）已关闭 |

## 验证

- `herdr server agent-manifests`：qodercli `source_kind="local override"`、
  `local_override_shadowing_remote=true`；
- `herdr agent explain wA:p2A`：manifest 指向本地覆盖文件；
- 上报链路：对已注册 agent 的 pane 手动触发钩子 →
  `agent_session.source="herdr:qodercli"` 立即出现；
- **端到端**：在受信任目录（`--cwd /Users/user/herdr`）按工厂同款命令
  `herdr agent start --kind qodercli -- --dangerously-skip-permissions` 起真实
  agent → `agent_session.value="012cff74-…"`（真实 CN CLI 会话 id），修复前该字段
  对 qodercli 从未出现过。pane 终端标题 `◇ Qoder CLI CN | Ready` 亦确认产品正确。

## 非规格决策与权衡（Out-of-spec decisions）

1. **保留 factory agent id `qodercli` 不改名为 `qodercn`**：herdr 工具
   `agent start --kind` 只接受内置 kind `qodercli`，改名需要在
   `services/herdr-worker.py` 增加 id→kind 映射并迁移
   `~/.herdr-controller/*.json` 中的存量状态（controller 带电，且当前有
   workflow 在跑），风险大于收益。"正确的是 qodercn"在产品/二进制层已满足。
2. **从国际版摘除钩子而非仅新增 CN 侧**：用户明确"两个不同的 Agent"——国际版
   qoder 会话不应再被上报为 factory 的 qodercli agent。回滚：恢复两个
   `.bak.herdr-fix-20260913-101041` 备份。
3. **用本地 manifest 覆盖而非等待上游**：remote manifest 由 herdr 服务分发，
   本地覆盖会 shadow 掉 qodercli 的 remote 更新（其他 agent 不受影响）；
   remote 有实质更新时需人工重放别名修正。
4. **验证成本**：消耗 2 次 CN CLI 交互会话 + 1 次 `-p` 调用（临时 pane，已关闭）；
   `-p` 会话因 cwd=/tmp 不受信任被 CLI 安全层拦截，属预期（见教训 §11）。

## 回归风险与遗留

- `herdr integration install qodercli` 会把钩子重新装回 `~/.qoder`（安装器硬编码），
  重跑后必须重做迁移；
- herdr 工具升级若修正安装器/别名，可删除本地覆盖回归 remote 管理；
- 观察项（与本次无关）：CN CLI 启动时告警 `5 errors loading agent configs`
  （`~/.qoder-cn/agents/` 下有损坏/不兼容的 agent 定义，待另行排查）；
  International Qoder 仍在 `/Applications`、`~/Applications` 各装一份 CN IDE。
