# 请求幂等与 SSE 回放

## 目标

客户端可在 `POST /agent/query` 中提交 `client_request_id`。系统以 `tenant_id + client_request_id` 为唯一键，避免网络重试、代理重发或用户重复提交造成第二次检索、重复曝光和重复 Session 消息。

## 执行规则

```text
首次请求 → 原子占用 → 执行工作流 → 保存完整 SSE → completed
相同载荷 + completed → 回放原 SSE，不执行工具
不同载荷 + 相同 ID → CLIENT_REQUEST_ID_CONFLICT
相同载荷 + processing → CLIENT_REQUEST_IN_PROGRESS
流中断/超时占用 → 标记 failed 或超时接管
```

请求指纹覆盖查询文本、租户、Session、用户、ACL 和 Top-K，不包含 `client_request_id` 自身。回放保留原服务端 `request_id`、结果、曝光 ID 和 Receipt ID，因此不会制造新的执行事实。

默认数据库为 `.runtime/request-replays.sqlite3`，可通过 `SEEKORA_REQUEST_REPLAY_DB_PATH` 修改。处理中占用默认 60 秒后可接管，避免进程异常退出永久锁死请求；完成或失败记录默认保留 24 小时，并在新请求占用时增量清理。SQLite 适合本地和单实例部署，多实例环境应通过 `RequestReplayStore` 替换为共享数据库或具备原子占用能力的缓存。

## 新增文件职责

- `src/seekora_agent/application/idempotency.py`：定义请求指纹、占用结果和持久化端口。
- `src/seekora_agent/infrastructure/stores/sqlite_request_replay.py`：用 SQLite 原子管理执行权并持久化完整 AgentEvent 序列。
- `tests/test_idempotency.py`：覆盖跨重启回放、载荷冲突和处理中保护。
- `docs/01-architecture/request-idempotency.md`：记录幂等键、指纹、安全边界和部署限制。
