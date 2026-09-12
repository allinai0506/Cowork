# 共事工厂控制台（Herdr Factory Console）

**产品名称：共事工厂**，产品标语：本地 AI 软件工厂。

产品名与标语以 `console/herdr_factory_console.py` 顶部的 `PRODUCT_NAME` / `PRODUCT_TAGLINE` 常量为唯一事实来源（first-class citizen）：页面标题、侧边栏品牌区均由该常量注入，禁止在 HTML/JS 中散落硬编码。前端界面文案统一使用中文，领域名词按 2026-09-12 用户确认的映射翻译：**Tab=工作流节点/阶段、Pane=智能体工位、Agent=执行者、Task=任务**（紧凑场景允许短形"工位/节点"）；Workflow、Space、`auto`、Router、agent 名称（opencode/codex/claude 等）与后端状态枚举保持原样。

这里是共事工厂控制台的仓库内 canonical source。此前 Console 只有部署副本 `~/.herdr-console/herdr_factory_console.py`，导致前端改动无法随仓库审查、同步和回滚。

## 技术栈

- **服务端**：Python 3，使用标准库 `http.server.BaseHTTPRequestHandler` 与 `ThreadingHTTPServer`，不依赖 Flask/FastAPI 等 Web 框架。
- **前端**：服务端脚本内嵌 HTML5、CSS3 和原生 JavaScript；通过 DOM API、`fetch` 和 `setInterval` 实现页面渲染、交互与轮询。
- **接口格式**：JSON over HTTP，统一返回 `{ok, data, error}` 结构；`/api/ops-center` 代理 `bin/herdr-task ops-center`。
- **数据来源**：`~/.herdr-controller/*.json` 任务/Workflow 注册表，以及 `herdr` CLI 的 Pane、Agent、Workspace 查询结果。
- **运行与部署**：macOS LaunchAgent `com.user.herdr-factory-console`，默认监听 `127.0.0.1:8765`；仓库源通过安装脚本同步为 `~/.herdr-console` 运行副本。
- **构建依赖**：无 npm、Node.js、SPA 框架或前端打包步骤；部署是 Python 文件和 AppleScript 的文件同步。

## 运行架构

```text
console/herdr_factory_console.py
        │ scripts/install-herdr-console.sh
        ▼
~/.herdr-console/herdr_factory_console.py
        │ com.user.herdr-factory-console LaunchAgent
        ▼
http://127.0.0.1:8765/
```

仓库源文件负责版本管理；`~/.herdr-console` 是运行时部署副本，不应直接作为长期开发位置。

## 部署

在仓库根目录执行：

```bash
./scripts/install-herdr-console.sh
```

脚本会同步 `herdr_factory_console.py` 与 `launcher.applescript`，然后按项目规范重启 `com.user.herdr-factory-console`。可使用 `--no-restart` 只同步文件。

## Dashboard V2 API

Console 提供：

```text
GET /api/ops-center
GET /api/ops-center?workflow_id=<id>&include_tasks=1
POST /api/run              -> 202 {job_id, status: "running"}
GET /api/run/status?id=<job_id>
```

接口代理仓库内的 `bin/herdr-task ops-center`。普通工厂页面通过“进入运维驾驶舱”入口展示老板视角、Workflow/Tab、Agent Fleet、异常中心和任务时长；进入后同一位置显示“← 返回工厂”，点击即可恢复进入前的 Space/Workflow。运维视图会隐藏依赖工厂上下文的操作按钮；点击 Workflow 卡片则同步切换到它所属的项目和 Space，再进入对应 Workflow/Pane 详情。

新需求启动采用异步 Job：Console 不会让浏览器请求同步等待 Deep Preflight 和总指挥派发；后台命令允许最多运行 600 秒。启动成功后关闭弹窗并提示 Workflow ID；失败时保留表单、恢复按钮，并展示后端返回的具体错误。

## 工作流模板库 API

Console 通过 `sys.path` 直接复用 `herdr.workflow` 的模板引擎（`list_templates` / `load_template` / `validate_workflow_dag`）：

```text
GET  /api/templates          -> {templates: [{id, label, version, node_count, path, is_builtin}]}
GET  /api/template?id=<name> -> {template, nodes(含 depends_on), yaml(原文), is_builtin}
POST /api/template           -> {name, yaml}；服务端 DAG 校验通过后写入 ~/.herdr-controller/templates/<name>.yaml
POST /api/run                -> body 支持 template 字段，透传 herdr-factory run --template
```

页面入口：动作区“模板库”（列表 / 节点依赖预览 / 查看 YAML / 新建与编辑自定义模板）；“新需求”弹窗提供“工作流模板”下拉框。内置模板（`workflow_templates/`）只读；自定义模板名限 `^[a-z0-9][a-z0-9_-]{0,63}$`（防路径穿越），保存时先做未知依赖 / 循环依赖校验，失败即拒绝写盘。模板变更只影响之后新启动的 Workflow，不改运行中实例。

## 变更规则

- 前端代码只修改 `console/`，通过安装脚本部署。
- 后端数据聚合只修改 `bin/herdr-task`。
- 修改后至少执行 Console 语法检查、`/api/ops-center` HTTP smoke test 和浏览器页面检查。
- 不把 `~/.herdr-console` 下的备份文件、日志或缓存同步进仓库。
