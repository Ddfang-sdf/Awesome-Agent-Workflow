# 后端与归因服务部署边界

## 目标

Telemetry 后端和归因服务使用两个独立制品、两个运行进程和两套版本号。两者可以分别部署、回滚和升级。

## 责任边界

| 组件 | 负责 | 不负责 |
| --- | --- | --- |
| Telemetry 后端 | 接收 Diff、校验和落盘、维护业务事务、调用归因接口、持久化归因结果 | 执行归因算法 |
| 归因服务 | 校验归因请求、执行 Mock 或真实引擎、返回版本化结果 | 访问后端数据库、修改遥测业务状态 |

后端先提交 Diff 确认和 `pending` 归因记录，再调用归因服务。远程调用失败时，Diff 保持已确认状态，归因记录变为 `failed`；重复上传相同 Diff 会重新触发失败记录的归因请求。

## 调用契约

后端调用：

```text
POST {AAW_TELEMETRY_ATTRIBUTION_SERVICE_URL}/api/v1/attributions
Idempotency-Key: {dev_run_id}
Authorization: Bearer {token}  # 配置 Token 时
```

请求和响应均携带 `schema_version=1.0`。归因服务升级时必须继续接受当前版本；需要不兼容变更时增加新版本，不得原地改变已有字段语义。

## 分别部署

先部署归因服务：

```bash
cd attribution-service
cp .env.example .env
docker compose up -d --build
```

再部署后端。后端 `.env` 中的地址必须是从后端容器可访问的归因服务地址：

```text
AAW_TELEMETRY_ATTRIBUTION_SERVICE_URL=http://attribution.internal:8010
AAW_TELEMETRY_ATTRIBUTION_TIMEOUT_SECONDS=10
AAW_TELEMETRY_ATTRIBUTION_API_TOKEN=replace-with-shared-secret
```

```bash
cd telemetry-server
docker compose -f compose.remote.yaml up -d --build
```

两个 Compose 项目互不包含对方的服务。单独升级归因服务不会执行后端数据库迁移；单独升级后端也不会重建归因服务。

## 升级检查

归因服务升级前验证其 `/health/ready` 和 `schema_version=1.0` 契约测试。后端升级前执行 Alembic 迁移并验证归因服务地址可达。任一服务回滚时，保留对另一服务当前契约版本的兼容性。
