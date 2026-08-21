# SQLite Session 与 Receipt

本阶段把短期会话状态和执行回执从进程内存迁移到 SQLite。应用层仍只依赖 `SessionStore` 和 `ReceiptStore` 端口，因此将来切换 Redis、PostgreSQL 或专用审计存储时不需要修改 Runtime。

## Session Store

`SQLiteSessionStore` 使用 `tenant_id + session_id` 联合主键，保存用户身份、消息 JSON、最近一次结构化意图、版本号、更新时间和过期时间。

- 默认 TTL 为 24 小时，每次成功写入都会续期；
- 默认最多保留 40 条消息，超出后裁剪最早消息；
- 写入使用版本号比较并交换，旧版本并发写入会抛出冲突；
- 同一租户会话创建后不能静默切换 `user_id`；
- 读取和写入时增量清理过期记录，过期会话按新会话处理。

消息裁剪控制 SQLite 记录大小，但它不是语义摘要。长对话的摘要压缩仍属于后续能力。

## Receipt Store

`SQLiteReceiptStore` 保存完整 Receipt JSON，并单独索引租户、Session、状态、开始时间、结束时间和写入时间。默认保留 30 天，查询或写入时增量清理过期数据。反序列化会恢复嵌套的工具调用回执，使 `/agent/receipts/{request_id}` 在进程重启后仍可返回完整结果。

## 默认数据库与配置

默认文件位于：

- `.runtime/sessions.sqlite3`
- `.runtime/receipts.sqlite3`

相关环境变量：

- `SEEKORA_SESSION_DB_PATH`
- `SEEKORA_SESSION_TTL_SECONDS`
- `SEEKORA_SESSION_MAX_MESSAGES`
- `SEEKORA_RECEIPT_DB_PATH`
- `SEEKORA_RECEIPT_RETENTION_SECONDS`

两个数据库都启用 WAL，适合本地和单实例部署。多副本生产部署仍应替换为共享状态服务，并补充集中备份、加密、数据删除审计和容量治理。

## 新增文件职责

- `src/seekora_agent/infrastructure/stores/sqlite_session.py`：实现 Session SQLite 适配器及其生命周期、裁剪和并发不变量。
- `src/seekora_agent/infrastructure/stores/sqlite_receipt.py`：实现完整 Receipt 的 SQLite 保存、恢复和保留期清理。
- `tests/test_sqlite_state.py`：验证两个 Store 的重启恢复、TTL、裁剪、身份隔离、版本冲突，以及 Runtime 重建后的端到端状态恢复。
