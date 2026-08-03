读取 `SR-design.md` 后，询问用户：此 SR 是否需要拆分 AR？`SR-design.md` 只提供 SR 级功能与技术事实；本步骤是 AR 身份和范围的唯一权威来源。

无论用户选择哪条分支，均必须生成 `.sdd/{SR}/AR-split.md` 后才能完成工作单。

拆分 AR 时：

1. 基于 SR-design.md 提出 AR 切分方案；
2. 对每个 AR 逐条向用户确认稳定 `id`、可读 `title` 和范围摘要；
3. 写入 `AR-split.md`：拆分结论、拆分理由，以及每个 AR 的 id、title、范围摘要和确认来源；
4. 范围摘要只定义该 AR 承接哪些 SR 级功能及其边界，不重复 SR 的接口、数据和架构事实。

确认后，收集每个 AR 的 id 和 title，例如：

- AR-001: 用户管理
- AR-002: 权限控制

确认后，构造：

```json
{"ars":[{"id":"AR-001","title":"用户管理"},{"id":"AR-002","title":"权限控制"}]}
```

不拆分时，在 `AR-split.md` 写明“免拆分：本 SR 以整体范围进入 module-boundary-design”，并记录确认来源；随后构造：

```json
{"mode":"no_split"}
```
