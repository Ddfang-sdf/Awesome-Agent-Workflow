# AAW CLI 本地运行日志

AAW CLI 默认把运行日志写入当前 Git 仓库根目录的 `.aaw/logs/`；当前目录不属于
Git 仓库时，以当前目录为根。`.aaw/` 已在仓库 `.gitignore` 中排除。

## 文件布局

```text
.aaw/logs/
├── system.log
└── workflows/
    └── <workflow-id>.log
```

绑定到具体 workflow 的命令按持久化 UUID 写入独立日志；`update`、`--help`、
`--version`、无效参数和无法绑定 workflow 的早期错误写入 `system.log`。每个文件达到
100 MiB 后滚动为 `.log.1`、`.log.2`。workflow 连续 30 天无活动后删除整组日志，
系统日志仅保留最近 30 天。

每一行采用便于人工阅读和检索的 Log4j 风格：

```text
2026-07-27 16:42:18.123 +08:00 INFO  [pid=1234 thread=MainThread workflow=<uuid> sr=SR-001 ar=- invocation=<uuid> seq=3] stdout - SR SR-001 已启动
```

CLI 会原样保留控制台行为，同时把 stdout 记为 `INFO`、stderr 记为 `ERROR`。
写入日志时移除 ANSI 终端控制码，并转义不可见控制字符。命令行中的常见 token、
password、secret 和 API key 参数会脱敏；stdout/stderr 内容不会自动脱敏，因此日志
可能包含需求、代码或其他敏感信息。

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `AAW_LOGGING` | `on` | 设为 `off` 可完全禁用本次 CLI 日志 |
| `AAW_LOG_LEVEL` | `INFO` | 可选 `DEBUG`、`INFO`、`WARN`、`ERROR` |

日志使用按文件粒度的跨进程锁，最多等待 2 秒。目录不可写、磁盘满或锁超时只会向
stderr 输出一次 warning，不会阻塞工作流主流程。
