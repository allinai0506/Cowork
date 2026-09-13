# Walkthrough: 控制台刷新后保持当前视图（视图状态持久化）

> 日期：2026-09-13 · 范围：`console/herdr_factory_console.py`（纯前端行为）· 回归测试：`tests/test_console_view_state.py`

## 问题

控制台（`http://127.0.0.1:8765`）是单页应用，但所有视图状态只存在 JS 内存变量 `state` 里：

- 服务端只在 `/` 返回 HTML，其余全是 `/api/*`，地址栏永远是根路径；
- 全文件无 localStorage / hash 路由 / History API。

因此浏览器刷新（Cmd+R）后 `state` 清零，`refreshAll()` 的空间 fallback 把用户踢回第一个 `current_factory` 空间（"首页"）；在运维驾驶舱里刷新还会掉回工厂视图。页面内的 10 分钟轮询不受影响（内存 state 还在），只有整页刷新/重开浏览器才丢状态。

## 修复

视图三元组 `{opsMode, spaceId, workflowId}` 写入 localStorage（key `herdrConsoleView`），启动时恢复：

1. 新增 `saveViewState()` / `loadViewState()` 辅助函数，均带 try/catch 静默降级（隐私模式 localStorage 不可用时不阻塞启动）。
2. 在所有视图切换点调用 `saveViewState()`：`showOpsCenter`、`exitOpsCenter`、`openWorkflowFromOps`（成功与回退两条路径）、`selectSpace`、`loadWorkflow`、`clearWorkflow`。
3. 启动引导改为：读取存储 → 恢复 `state` 三元组 → `state.opsMode ? showOpsCenter() : refreshAll()`。
   - 运维模式恢复走 `showOpsCenter()` 而非 `refreshAll()` 的 ops 分支，因为只有前者会写 `运维驾驶舱` 标题文案，否则刷新后标题停留在 boot 时的 "选择项目"。
4. 脏数据自愈完全复用现有护栏，零新增校验代码：
   - 恢复的 `spaceId` 已失效（空间被删）→ `refreshAll()` 既有 `!ss.some(...)` 检查回退到首选空间；
   - 恢复的 `workflowId` 不属于当前空间的项目 → `loadProject()` 既有检查回退到 `latest_workflow_id`；
   - 历史/未注册空间 → `selectSpace()` 清空 workflowId 并顺带清洗存储。

## 规格外决策与权衡

- **localStorage vs URL hash 路由**：选 localStorage。hash 路由可分享/收藏深链，但该控制台是单文件内嵌前端、无服务端路由，hash 方案改动面大且无使用场景；用户痛点只是"刷新别丢页面"。
- **只持久化三元组，不持久化 projectId**：projectId 可由 space→project join 推导（`selectSpace` 内部完成），持久化它会引入第二份可能互相矛盾的真相。项目内其它状态（模板选择等）为会话态，丢失无害。
- **`loadWorkflow` 在 API 成功后才 `saveViewState()`**：失败时不落盘，避免把一个拉取失败的 workflowId 固化进存储（即使落了，`loadProject` 护栏也能自愈，此处取保守路径）。
- **每次点击都会整串 JSON 覆写 localStorage**：数据量 <200B，10 分钟轮询也仅是幂等覆写，无性能与交互影响（符合 wiki 记录的"轮询区签名守卫"约束——本改动不触碰任何轮询重渲染路径）。
- **10 分钟轮询 / visibilitychange 不读存储**：恢复逻辑只在启动引导执行一次，运行中的轮询继续以内存 state 为准，避免轮询期间用户的切换被存储里的旧值覆盖。

## 验证（2026-09-13 实测）

- `python3 -m py_compile` + 内嵌 JS 提取后 `node --check` 通过；
- `tests/test_console_view_state.py` 新增 5 个断言（持久化字段完整性、降级路径、六个切换点全覆盖、启动恢复、ops/factory 分支），console 全量 4 个既有测试文件 + 新文件共 48 个用例通过；
- `scripts/install-herdr-console.sh` 部署 + LaunchAgent 重启后，Browser Use（IAB）浏览器实测通过：
  1. 切换 Workflow → `reload()` → 仍停留原 Workflow（switcher 选中值与标题均为 `wf-...-230101`，不再回落到最新 Workflow）；
  2. 切换空间（数凭电子会计档案 wA）→ `reload()` → 仍停留 wA，localStorage 落盘 `{"opsMode":false,"spaceId":"wA","workflowId":"wf-nexusarchive-54433229-20260913-084418"}`；
  3. 进运维驾驶舱 → `reload()` → 仍在驾驶舱（标题、33 张 Workflow 卡片、工厂按钮隐藏均正确），且空间/Workflow 三元组同时保留；
  4. 退出驾驶舱 → 回到之前的工厂视图（wA + 原 Workflow），再 `reload()` 一次仍正确恢复。
- 实测踩坑记录（工具侧，非页面 bug）：IAB 内 reload 后，指向工具栏右上角（约 x=1189,y=47）的 Playwright locator click 与 cua 坐标 click 均不触发 handler（同位置 reload 前正常、其它位置 reload 后正常、DOM `element.click()` 正常）——IAB 面板角落遮挡怪癖；另外 reload 后数据链路（overview→project→workflow 串行子进程）需 5~10s 才渲染完，验证脚本必须轮询等待而非固定短睡。
