# SR 原始需求保留与遗漏门禁方案

## 1. 背景

当前 SR 入口只要求提供 `SR`，用户在启动工作流时描述的原始需求主要存在于
对话上下文中，没有成为工作流的正式输入。

现有链路为：

```text
用户在对话中描述原始需求
  → sr-init
  → sr-design
  → sr-design-gate
```

其中：

- `sr-design` 依赖当前对话理解原始需求；
- `sr-design-gate` 的正式输入只有 `software_architecture.md` 和 `SR-design.md`；
- Gate 可以检查 `SR-design.md` 内部是否完整、一致，但无法判断它是否遗漏了对话中的
  某项原始需求。

因此可能出现以下情况：

1. `sr-design` 在生成文档时遗漏一项原始需求；
2. 设计文档剩余内容内部自洽；
3. Gate 没有原始需求作为对照，最终错误通过。

## 2. 目标

本方案只解决一个问题：让原始需求成为 SR 工作流中可持久读取的正式输入，使
`sr-design` 和 `sr-design-gate` 都能据此检查遗漏。

目标如下：

1. SR 启动时原样保存用户提供的原始需求；
2. 保存过程不依赖后续会话上下文或模型记忆；
3. `sr-design` 必须读取原始需求后再生成设计；
4. `sr-design-gate` 必须拿原始需求反查设计覆盖情况；
5. 任一明确原始需求没有设计落点时，Gate 不得通过。

## 3. 非目标

本方案不引入以下能力：

- 不新增工作流节点；
- 不建立独立的需求基线管理流程；
- 不要求为每条需求分配稳定 ID；
- 不新增 JSON 追溯产物；
- 不引入需求版本管理或审批系统；
- 不引入需求变更管理流程（SR 进行中的需求变更处理见 8.4，仅定义最小规则）；
- 不改变现有 `sr-design → sr-design-gate → ar-split` 主流程。

## 4. 总体方案

为 SR 入口增加原始需求文件参数。CLI 在创建 workflow 时，将该文件内容原样保存到
SR 目录：

```text
aaw start --entry sr --sr SR-001 --requirement-file <path>
```

固定保存为：

```text
.sdd/SR-001/original-requirement.md
```

后续链路为：

```text
原始需求文件
  → aaw start --entry sr
  → .sdd/{SR}/original-requirement.md
       ├─→ sr-design
       └─→ sr-design-gate
```

`original-requirement.md` 是只读来源文件。`sr-design` 和 `sr-design-gate` 可以读取，
但不得修改或覆盖。

## 5. SR 入口如何保留需求

### 5.1 CLI 接口

为 `aaw start` 增加参数：

```text
--requirement-file PATH
```

使用示例：

```bash
uv run <skill-dir>/scripts/aaw.py start \
  --entry sr \
  --sr SR-001 \
  --requirement-file ./requirement.md \
  --json
```

规则：

1. `--entry sr` 时必须提供 `--requirement-file`；
2. 文件必须存在、可读且内容非空；
3. 文件按 UTF-8 读取；
4. CLI 不总结、不改写、不拆分文件内容；
5. CLI 将内容写入 `.sdd/{SR}/original-requirement.md`；
6. 目标文件已存在时的处理：
   - 已存在内容与本次输入**逐字节一致**：视为幂等成功，不重复写入、不报错（用于 `workflow.yaml`
     写入失败后重试、或重复执行 `start` 的场景）；
   - 在 `workflow.yaml` 尚不存在的启动恢复场景中，已存在内容**不一致**：启动失败并提示
     用户显式处理（确认保留旧文件，或手动删除冲突文件后重启），不得静默覆盖；
7. 原始需求保存失败时，不得返回启动成功；
8. 启动过程中任一步失败时，必须回滚本次 `start` 新建的产物（新建的 SR 目录、半写的
   原始需求文件），不得残留一个缺少 `workflow.yaml` 或内容不完整的目录。已存在于启动前的
   文件不在回滚范围内。

`--entry ar` 不要求该参数，保持现有行为。

### 5.2 对话入口

用户通常直接在对话中描述需求，而不是预先提供本地文件。此时
`aaw-workflow` 按以下方式启动：

1. 提取用户明确作为“原始需求”提供的文本；
2. 保持原文，不进行总结或设计性改写；
3. 将原文写入临时 Markdown 文件；
4. 使用该临时文件调用 `aaw start --requirement-file`；
5. 以 CLI 返回的成功结果为准，不依赖临时文件作为后续输入。

如果需求分布在同一轮用户输入的多个段落中，按原顺序完整保存。普通讨论、
Agent 的解释以及后续设计推导不得混入原始需求。

如果无法判断用户是否已经提供了原始需求，工作流应先向用户收集需求，而不是以空内容
启动 SR。

**保存结果核对**：原文提取到落盘之间没有机器校验，为防止 Agent 无意改写，`start`
成功后 Agent 必须向用户回显已保存的 `original-requirement.md` 内容（内容较长时可回显
开头若干行并注明总行数），请用户确认与其提供的原始需求一致。用户指出不一致时，必须
停止推进且不得执行 `next`；用户提供或确认正确原文后，修正当前 workflow 的
`.sdd/{SR}/original-requirement.md`，重新回显核对，再继续当前 workflow。此时不得重新
执行 `start`，因为该 SR 的 `workflow.yaml` 已经存在。该纠错仅修正入口提取错误，不属于
设计过程中的需求变更。

### 5.3 持久化时机

原始需求必须在 `start` 成功返回前落盘。推荐由 `WorkflowManager.start` 在同一次启动
操作中完成目录创建、原始需求写入和 `workflow.yaml` 写入。

启动顺序应满足：

```text
校验参数和源文件
  → 创建 SR 目录
  → 写入 original-requirement.md
  → 写入 workflow.yaml
  → 返回启动成功
```

若中途失败，CLI 返回失败，不得产生一个对外宣称已成功但缺少原始需求的 workflow。

原始需求正文不写入 `workflow.yaml`，避免长文本重复、YAML 转义问题以及状态文件膨胀。

## 6. sr-design 调整

### 6.1 工作单输入

在 `sr-design.yaml` 中增加 required input：

```yaml
input:
  - path: ".sdd/software_architecture.md"
    required: false
  - path: ".sdd/{SR}/original-requirement.md"
    required: true
```

原始需求缺失时，`sr-design` 工作单必须处于 blocked 状态，不得继续生成设计或执行
`done`。

### 6.2 Skill 规则

`sr-design` 增加以下要求：

1. 从工作单路径读取 `original-requirement.md`；
2. 将它作为用户需求的权威原文，而不是依赖会话记忆重建；
3. 生成设计后，逐项检查原始需求是否在设计正文、AR 拆分和需求追溯章节中有落点；
4. 原始需求与后续用户澄清冲突时，必须向用户确认，不得自行丢弃原始内容；
5. 不得修改 `original-requirement.md`。

现有设计模板第 8 章“需求追溯”继续复用，不新增单独追溯文件。表中的“系统需求（SR）”
应能反查到原始需求中的对应内容。

**范围裁剪的记录要求**：经用户确认为范围外、延期或不实现的原始需求，`sr-design`
必须在需求追溯表中显式记录一行，至少包含：原始需求原文引用（或可定位的原文位置）、
处置结论（范围外/延期/不实现）、用户确认来源（哪次澄清问答，如问题池编号）。这是
Gate 核查裁剪合法性的唯一依据——只存在于对话记忆中、未落入文档的确认视为无确认。

## 7. sr-design-gate 调整

### 7.1 工作单输入

在 `sr-design-gate.yaml` 中增加 required input：

```yaml
input:
  - path: ".sdd/software_architecture.md"
    required: true
  - path: ".sdd/{SR}/original-requirement.md"
    required: true
  - path: ".sdd/{SR}/SR-design.md"
    required: true
```

### 7.2 Gate 检查规则

在现有“需求完整性”和“需求追溯性”检查中增加原始需求反查：

1. 读取完整原始需求，不得仅依据 `SR-design.md` 中已经整理过的需求列表；
2. 按原始需求中的明确条目、段落和约束逐项检查；
3. 每项明确需求必须在 `SR-design.md` 中找到可定位的设计落点；
4. 该设计落点必须继续关联到 AR 或明确说明由 SR 整体承接；
5. 原始需求明确要求的可验收行为必须在 SR 验收标准中有对应覆盖；
6. 标记为范围外、延期或不实现的原始需求，必须在 `SR-design.md` 需求追溯表中有
   6.2 节要求的显式裁剪记录（原文引用、处置结论、用户确认来源）。Gate 只认文档内
   的显式记录：追溯表中无记录、或记录缺少用户确认来源的裁剪，一律视为静默省略。

结论规则：

- 明确原始需求没有设计落点：记 P1，结论为 `不通过`；
- 原始需求被裁剪但缺少 6.2 节要求的显式记录（含缺少用户确认来源）：视为静默省略，
  记 P1，结论为 `不通过`；
- 设计内容与原始需求发生实现方向或接口语义冲突：沿用现有 P0/P1 分级；
- 无法判断某段原文是否属于本次范围，且没有用户确认：列为待确认，结论为 `阻塞`；
- 原始需求缺失或不可读：结论为 `阻塞`；
- 所有原始需求均有明确设计和验收落点：继续执行现有其他 Gate 检查。

门禁报告恒定生成：每一轮检查都必须生成或更新 `SR-design-gate.md`，零问题时也必须在
报告中写入原始需求反查结论；发现遗漏时必须在报告中指出原始需求原文及缺失位置。

## 8. 兼容性处理

### 8.1 新建 SR

新版本创建的 SR workflow 必须提供原始需求文件。缺失时 `start` 直接失败。

### 8.2 已有但尚未经过 Gate 的 SR

如果已有 workflow 不存在 `original-requirement.md`：

- 不猜测或从 `SR-design.md` 反向生成原始需求；
- 在 `sr-design` 或 `sr-design-gate` 工作单中显示 required input 缺失；
- 提示用户补充真实原始需求文件后继续当前步骤；
- 不自动 rollback。

### 8.3 已完成 Gate 的 SR

已经通过 Gate 并进入下游的历史 workflow 不自动回退，也不强制补跑 Gate。

### 8.4 SR 进行中的需求变更

完整的需求变更管理不在本方案范围（见非目标）。本方案只保证原始需求作为只读来源
不被 Agent 悄悄改写，因此定义如下最小规则：

- `sr-design` / `sr-design-gate` 执行过程中，Agent 不得修改或覆盖
  `original-requirement.md`；
- 用户在设计中途提出真实的原始需求变更时，Agent 不自行改写来源文件，而是提示用户，
  由**用户本人**更新当前 workflow 的 `original-requirement.md`；
- 来源文件更新后，`sr-design` 需据新原文复查设计覆盖，`sr-design-gate` 以更新后的
  原文重新反查。变更前已生成的设计落点是否仍成立，由这两步正常检查发现，不额外引入
  基线比对机制。

## 9. 主要改动点

### 9.1 CLI

- `skills/aaw-workflow/scripts/cli/main.py`
  - 为 `start` 增加 `--requirement-file`；
  - 读取并校验文件；
  - 将原始需求交给 WorkflowManager。
- `skills/aaw-workflow/scripts/cli/workflow.py`
  - SR 启动时持久化 `.sdd/{SR}/original-requirement.md`；
  - 确保保存失败时启动失败；
  - 防止静默覆盖已有原始需求。

### 9.2 工作流定义

- `skills/aaw-workflow/scripts/cli/definitions/sr-design.yaml`
  - 增加 required 原始需求输入。
- `skills/aaw-workflow/scripts/cli/definitions/sr-design-gate.yaml`
  - 增加 required 原始需求输入。

### 9.3 Skills

- `skills/aaw-workflow/SKILL.md`
  - SR 启动时收集并原样传递用户需求；
  - 更新 SR 启动命令说明。
- `skills/sr-design/SKILL.md`
  - 从工作单读取原始需求；
  - 增加原始需求覆盖自查；
  - 范围裁剪需在需求追溯表显式记录（原文引用、处置结论、用户确认来源）。
- `skills/sr-design-gate/SKILL.md`
  - 增加原始需求反查流程。
- `skills/sr-design-gate/references/gate-checklist.md`
  - 补充遗漏判定和结论规则。

## 10. 测试方案

### 10.1 CLI 测试

1. SR 入口提供有效需求文件，成功创建 workflow 和
   `.sdd/{SR}/original-requirement.md`；
2. 保存后的内容与输入文件一致；
3. SR 入口未提供 `--requirement-file`，启动失败；
4. 文件不存在、不可读或为空时，启动失败；
5. 目标原始需求文件已存在且内容一致时，幂等成功；内容不一致时启动失败，不覆盖；
6. 启动中途失败（如 `workflow.yaml` 写入失败）时，本次新建的 SR 目录和原始需求文件
   被回滚，重试 `start` 可成功；
7. AR 入口保持现有行为，不要求原始需求文件。

注意：`--entry sr` 必填是 CLI 行为变更，`test/aaw_workflow` 中现有的 `start_sr`
测试 helper 及所有依赖它的用例需同步适配（统一提供一个 fixture 需求文件）。

### 10.2 工作流测试

1. `sr-design` 工作单包含 required 原始需求输入；
2. `sr-design-gate` 工作单包含 required 原始需求输入；
3. 原始需求缺失时，对应工作单 blocked；
4. 原始需求存在时，现有工作流推进逻辑不变。

### 10.3 Gate 评测

至少增加两个样本：

- 遗漏样本：原始需求包含一项明确能力，但 `SR-design.md` 完全未体现，预期 Gate
  判定 `fail` 并记录 P1；
- 完整样本：原始需求全部在设计、AR 和验收中有落点，预期该检查维度通过。

## 11. 验收标准

方案实现后应满足：

1. 任意新建 SR 都能在 `.sdd/{SR}/original-requirement.md` 找到启动时的需求原文；
2. 删除该文件后，`sr-design` 和 `sr-design-gate` 均无法继续；
3. 从 `SR-design.md` 中删除一项原始需求的全部设计落点后，Gate 必须判定不通过；
4. 原始需求全部覆盖时，不影响现有 Gate 的其他检查和工作流流转；
5. 整个修改不增加新的工作流节点和额外交付流程。
