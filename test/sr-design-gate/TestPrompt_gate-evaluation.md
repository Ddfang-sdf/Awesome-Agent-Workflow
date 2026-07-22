# SR Design Gate 运行 Prompt

将下方 prompt 中的 `{WORKDIR}` 替换为 fixture 的绝对路径，并将 `{CASE}` 替换为
`conflicts` 或 `pass`。

=== BEGIN PROMPT ===

请使用 `$sr-design-gate` 审查当前 SR 设计。先完整读取该 Skill、`gate-checklist.md`、
`gate-result-template.md` 与当前 `sr-design` 模板；不得修改被审查的架构或 SR 设计。

测试样本位于 `{WORKDIR}`：

- 架构基线：`{WORKDIR}/software_architecture.md`
- SR 设计：`{WORKDIR}/SR-design.md`
- 样本类型：`{CASE}`

严格执行 Skill 的全部检查步骤，建立关键事实台账，逐项检查十个准入维度与全部跨章节
一致性链。不要读取 `expected-conflicts.md`，它只能在门禁完成后用于人工核对。

在当前会话输出完整的门禁结论和以下 JSON：

```json
{
  "gate_result": "pass | fail | blocked",
  "recommendation": "...",
  "report": "路径或 null",
  "summary": {
    "unqualified_dimensions": 0,
    "p0_conflicts": 0,
    "p1_conflicts": 0,
    "p2_findings": 0,
    "pending_questions": 0,
    "blocking_issues": 0
  }
}
```

若存在任意发现，按结果模板在 `{WORKDIR}/SR-design-gate.md` 写入报告；首次零问题通过时
不得创建该文件。完成后停止，不要继续 AR 拆分。

=== END PROMPT ===
