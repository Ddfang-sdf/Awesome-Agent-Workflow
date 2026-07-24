# SR Design Gate 评测用例

## 目标

验证 `sr-design-gate` 能按当前 SR 模板完成全量跨章节检查：冲突样本必须阻断，
通过样本必须在没有历史报告时给出零问题 `pass`。

## 运行步骤

1. 将 `fixtures/case-01-conflicts/` 或 `fixtures/pass/` 复制到干净工作目录。
2. 使用 `TestPrompt_gate-evaluation.md`，将 `{WORKDIR}` 替换为该目录的绝对路径，
   并将 `{CASE}` 替换为 `conflicts` 或 `pass`。
3. 对冲突样本，按 `fixtures/case-01-conflicts/expected-conflicts.md` 核对全部 10 项。
4. 对通过样本，确认结论为 `pass`，6 个 summary 计数均为 0，且没有创建门禁报告。

## 通过标准

- `conflicts`：结论必须为 `fail`，10 个预置冲突均被定位并给出整改要求；不得放行。
- `pass`：结论必须为 `pass`，`report=null`，六个 summary 计数均为 0。
- 建议每个样本以固定模型配置运行 3 至 5 次；冲突样本必须每次 10/10 命中，通过样本
  不得产生阻断性误报。

## 维护要求

SR 模板或 checklist 变更时，必须同步更新两个 fixture，并重新运行本评测。新增冲突时，
在 `expected-conflicts.md` 中增加稳定编号和命中条件。
