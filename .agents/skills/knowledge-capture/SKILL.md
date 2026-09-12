---
name: knowledge-capture
description: >
  在 session 收尾时，将本次排查的复杂 Bug、技术教训或架构决策，
  按四段式规范提取并追加到 docs/lessons/lessons-learned.md。
  触发词："沉淀一下知识"、"归档教训"、"knowledge-capture"。
triggers:
  - "沉淀一下"
  - "归档教训"
  - "knowledge capture"
  - "knowledge-capture"
---

# Knowledge Capture Skill

## 触发时机

Session 收尾或合并 PR 前，执行 **30 秒判定三问**：

| 判定问题 | 评估标准 | 处置动作 |
|----------|----------|----------|
| Q1：是否具备跨模块通用价值？ | 单点业务字段写错 vs. 接口契约/并发/错误透传等通用规律 | 仅单点 bug → 记 RCA 或不记；有通用规律 → 准入 |
| Q2：现有文档是否已有同类记录？ | `grep` 关键词检查 `docs/lessons/` | 已存在 → 补充到已有章节；不存在 → 准入 |
| Q3：是否有可验证的真实铁证？ | 错误日志、TraceID、报错堆栈、Git Commit 或 PR 编号 | 无铁证 → 拒绝沉淀；有证据 → 准入 |

## 执行步骤

### Step 1：快速检索现有教训

```bash
grep -n "###" docs/lessons/lessons-learned.md | head -40
# 确认是否已有同类章节
```

### Step 2：提取本次 session 的铁证

收集以下信息（至少满足其中一项）：
- Git Commit hash / PR 编号
- 错误日志片段（含时间戳）
- 报错堆栈
- 触发复现的命令

### Step 3：按四段式模板追加章节

参考模板文件：[`lessons-section.md`](./lessons-section.md)

```bash
# 追加到主文件末尾（---分隔符之前）
# 注意：章节编号取现有最大编号 +1
```

### Step 4：判断是否需要升格为门禁

- 若同类问题已在 `lessons-learned.md` 出现 **≥2 次** → 立即写 pytest 检查或 pre-push 脚本；
- 将门禁实现后，在对应章节的"操作规范"中注明"已固化到 XXX"。

### Step 5：提交

知识更新必须与本次代码变更**同一个 commit 提交**，禁止事后单独补提。

```bash
git add docs/lessons/lessons-learned.md
git commit --amend --no-edit   # 追加到本次 commit，或单独 commit
```

## 升格决策树

```
同类问题第几次出现？
    ├── 第 1 次 → 追加到 lessons-learned.md
    ├── 第 2 次 → 追加 + 立即写自动化门禁
    └── 第 3 次 → 说明门禁未生效，检查门禁覆盖范围
```

## 归档决策

当某条教训满足以下任一条件，将其移入 `lessons-archive.md`：
- 对应模块已完全重构，教训不再适用
- 已有 100% 覆盖的自动化门禁，手动规范已冗余
- 距离最后一次引用超过 6 个月且无复发
