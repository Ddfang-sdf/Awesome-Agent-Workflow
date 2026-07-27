# AAW CLI 本地运行日志

日志默认位于 Git 仓库根目录的 `.aaw/logs/`；非 Git 环境使用当前目录：

```text
.aaw/logs/
├── system.log
└── workflows/
    └── <workflow-id>.log
```

workflow 命令按持久化 UUID 写入独立文件，全局命令和无法绑定 workflow 的错误写入
`system.log`。日志采用带时间、等级、进程、线程、workflow/SR/AR、invocation、位置
和信息的 Log4j 风格。stdout 为 `INFO`，stderr 为 `ERROR`。

单个文件达到 100 MiB 后滚动；workflow 连续 30 天无活动后删除整组日志，系统日志
保留最近 30 天。写入失败只产生 warning，不影响 CLI 主流程。

- `AAW_LOGGING=off`：禁用日志。
- `AAW_LOG_LEVEL=DEBUG|INFO|WARN|ERROR`：设置最低等级，默认 `INFO`。

日志会脱敏命令行中的常见凭据参数，但不会修改 stdout/stderr 内容；排障和共享日志时
仍需注意需求、代码、路径和其他敏感信息。
