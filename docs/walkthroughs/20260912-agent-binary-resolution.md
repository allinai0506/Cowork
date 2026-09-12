# Implementation Notes

> 运行记录：实现中做出的、规格之外的决策 / 变更 / 权衡。按时间倒序追加新条目。

---

## 2026-09-12 · Agent CLI 二进制解析治本修复（执行者阵容误判"未安装"）

**需求**：console"执行者阵容"把已安装的 codex/claude/qodercli/agy 显示为"未安装"；采用治本方案——公共解析兜底，console 与后端探测共用。

### 根因（比上次修复更深一层）

- 上次修复（§6/commit 7d6dc5a）只修了二进制名映射与认证提示，探测仍走裸 `shutil.which`；
- console/controller 都是 LaunchAgent，plist PATH 不含 `~/.volta/bin`、`~/.local/bin`、`~/.qoder-cn/entry`，故 volta/用户目录安装的 CLI 被误判；homebrew 的 opencode/pi 正常——与截图症状完全吻合；
- 部署副本与仓库 md5 一致，排除"忘部署/忘重启"。

### 决策与权衡

1. **公共模块落点：新建 `herdr/agent_binary.py`，而非放进 `herdr/preflight.py`。**
   `preflight.py` 同时是 CLI 脚本和包模块，把"映射 + 解析"放它里面会让 console 依赖一个带 argparse main 的重模块；独立小模块（仅 stdlib 依赖）零循环导入风险，`herdr/__init__.py` → preflight → agent_binary 的链路安全。两个 bin 入口（herdr-preflight / herdr-deep-preflight）都会把仓库根加进 `sys.path` 后以包方式导入，绝对导入可靠；仍给两个库文件加了 try/except 引导，防直接 `python3 herdr/xxx.py` 运行时 ImportError。
2. **解析顺序：`shutil.which` → `EXTRA_BIN_DIRS` → 登录 shell `command -v`。**
   which 优先尊重显式 PATH 覆盖；目录兜底毫秒级且确定性（ volta shim 按自身位置解析，绝对路径执行无需 PATH）；zsh `-lic` 兜底最慢（≤8s/个）放最后，只在真缺失时触发——全部就位时零开销。此顺序与 deep_preflight 原实现（which → zsh → 目录）略有不同，属有意调整：快路径前置。
3. **`EXTRA_BIN_DIRS` 保留了 homebrew 两个目录**，虽然它们已在 LaunchAgent PATH 里——对 which 命中场景是冗余，但让模块在 cron/CI 等 PATH 更精简的环境下同样自洽。
4. **`AGENT_BINARIES` 三处副本收敛为一处**（console / preflight / deep_preflight 全改 import）；**`AUTH_HINTS` 未收敛**，是刻意取舍：console 对 agy 配了 `~/.agy` 提示（显示"认证 未配置"），而 preflight/deep_preflight 对 agy 为空（显示"未知"），统一必然改变某端 UI 文案，超出本 bug 范围。遗留为已知重复，登记在 wiki/common-change-paths。
5. **zsh 兜底 shell 取 `$SHELL` 环境变量**（默认 /bin/zsh），deep_preflight 原来写死 `/bin/zsh`——行为等价但可移植。
6. **测试为纯 hermetic 单测**（tmp 目录 + monkeypatch），不依赖本机真实安装位置；未写"六个 Agent 必须解析成功"的机器耦合断言，真实验证靠线上 API 而非 pytest。
7. **只重启了 console 服务，未动 controller plist/服务**：controller 本身不直接 import preflight；启动门禁走的 `bin/herdr-deep-preflight` 是每次新起进程，代码即改即生效。

### 验证

- `pytest tests/` 全量 112 passed（新增 11：解析器 8 + preflight.inspect 接入 1 + console roster 2）；
- `bash scripts/install-herdr-console.sh` 部署重启后，`GET /api/project?id=nexusarchive-54433229` 六 Agent 全部 `ready` 且 binary 为绝对路径；
- `env -i` 模拟 LaunchAgent 精简 PATH，四枚此前误判的 Agent 全部解析成功。

### 同步产物

wiki（preflight-and-health §2 / common-change-paths §2-3 / index 路由行 / log）、lessons-learned §8、本文件。
