# 预期语义冲突

1. `CONFLICT-001`：§4 超时 3 秒，§7 和 §8 为 5 秒。
2. `CONFLICT-002`：§5.7 没有 `CANCELING`，§8 使用该状态。
3. `CONFLICT-003`：§6 的 P99 为 200ms，§8 为 500ms。
4. `CONFLICT-004`：§5.8 的 `requestId` 为 integer，§4 为 string。
5. `CONFLICT-005`：§5.1 和 §5.9 失败后重试，§5.4 和 §8 直接终止。
6. `CONFLICT-006`：§5.2 存在软件架构禁止的反向依赖。
