# Console UI 网格基线对齐治理(snapping-ui-to-grid)

> 日期:2026-09-13 · 范围:`console/herdr_factory_console.py`(内嵌 HTML/CSS/JS)
> 依据技能:`~/.agents/skills/snapping-ui-to-grid`(四基准线 + 五动线分区 + 间距白名单)

## 背景与诊断结论

对运行中的共事工厂控制台(8765)按网格纪律做诊断,4 条基准线 3 条失守:

- **右轴(最严重)**:按钮组 `[＋新需求(primary)][模板库]…[查看日志]`,主按钮在最左;
  ops 按钮 `prepend` 且带 `btn primary`,工厂模式下双主按钮。
- **左轴**:主区 4 条假左轴(h3/task 15px、metric 14px、stage 13px、agent 行 14px 内距)。
- **数字轴**:metric 数量指标无等宽、无 `tabular-nums`。
- **间距白名单**:违规 36 处 vs 合规 15 处(违规率 71%),JS 内联样式亦违规。
- 工具栏轴线 ✓(同排同高);五分区中核心行动区过载(8 按钮)。

## 改动清单

| 层 | 改动 |
|----|------|
| P0 右轴 | 按钮组重排为 [查看日志][进入下一阶段][创建候选分支][指定执行者][执行者自检][模板库][＋新需求 primary];ops 按钮 className 改为按模式切换 `state.opsMode?'btn primary':'btn'`(消除双主按钮),`prepend` 保留(最左=次要起点) |
| P1 左轴 | `.main` 22→24px;所有卡片容器内距统一 16px(h3/task/metric/stage/agent-row/modal-head),消除 13/14/15 混排 |
| P1 白名单 | CSS 30 处 + HTML 1 处 + JS 内联 7 处裸值全部吸附到 4/8/16/24;`.wf-switcher` 负 margin `-6px` 移除 |
| P2 数字轴 | `.metric b` 加 ui-monospace + `font-variant-numeric:tabular-nums`;toast 18/18→24/24 对齐右轴 |
| 换行态 | 新增 `.actions .btn.primary{margin-left:auto}`:窄屏按钮换行后主按钮仍贴右缘 |

## 规格外决策与权衡

1. **两级轴的解释**(与技能字面要求的偏差):
   技能要求"页面 H1 与表格首列同一条 X"。本布局是带 1px 边框的卡片嵌在带 padding
   的主容器里,卡片内内容在几何上不可能落到页面轴(需要负内距)。故解释为:
   **容器轴(24px,页面级元素)+ 唯一卡片内轴(16px,所有卡片内容)**,每级只有
   一条轴、轴值全在白名单——治理前是 13/14/15 三条意外内轴,治理后为零意外轴。
2. **放弃 :root spacing token 化**(偏离诊断时承诺的方案):CSS 是单行压缩字符串,
   36 处 `var()` 包装会显著膨胀 diff 且 `font-size:14px` 与 `padding:14px` 共存使
   盲替换高危。改用"白名单直方图脚本"作为纪律门禁(见验证),效果等价、风险更低。
3. **平手值吸附方向**:12/20/6 等到 8/16 距离相同时,默认向下保密度;两处例外——
   `.btn` 水平 12→16(标签净空/命中区)、`.project` 12→16(卡片呼吸感)。
4. **toast bottom 18→24**:就近应为 16,取 24 是为与 right:24 呈直角对称、且 24
   同为白名单值(轴纪律优先于就近规则)。
5. **border-radius 豁免**:9/10/12px 圆角不是 spacing,不在白名单管辖内,保留。
6. **ops 按钮定位**:`prepend` 保留使其处于次要链最左;ops 模式下它是唯一可见按钮,
   此时恢复 primary 样式(唯一主按钮,合规)。
7. **按钮组重排保序**:次要按钮按原相对顺序反转,未重新设计信息架构;
   「行动区 8 按钮瘦身(收纳低频项)」属产品决策,**未做**,留待后续。

## 测试护栏

`tests/test_console_run_job.py` 等用 `assertIn` 钉住 `state.opsMode?'← 返回工厂':
'进入运维驾驶舱'`、`document.querySelectorAll('.factory-action')`、`>模板库</button>`
等子串——本次全部避开,未改任何被锁字符串。

## 验证证据

```bash
# 间距白名单直方图(CSS + JS 内联)→ {'16':20,'8':20,'4':7,'24':1} 违规:无
# 技能自带 grep → 零命中
sed -n '447p' console/herdr_factory_console.py | \
  grep -E '(padding|margin|gap)[^:;}]*:[^;}]*[^0-9.](5|6|7|9|11|13|14)px'
pytest tests/test_console_run_job.py tests/test_console_templates.py \
  tests/test_console_view_state.py tests/test_console_agent_roster.py \
  tests/test_console_stage_summary.py   # 48 passed
bash scripts/install-herdr-console.sh   # 部署到 ~/.herdr-console,8765 已加载新版
```

截图证据:1680 宽(主按钮贴右轴、metric 等宽、双主按钮消除)、960 宽(换行后
主按钮贴右)。诊断前对照:`/tmp/console_desktop.png`(治理前)。
