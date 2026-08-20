# 行为事件持久化队列与迟到处理

本增量把反馈写入从“直接调用行为 Store”改为 store-and-forward 管道：曝光校验通过后先进入持久化队列，再以幂等方式投递行为 Store。队列保留处理状态，使 Sink 临时失败的事件能够安全重放。

## 处理顺序

```text
Consent 检查
→ 服务端曝光校验与归因标准化
→ 服务端 User-Agent 机器人过滤
→ 迟到/异常时间分类
→ SQLite 队列幂等入队
→ 行为 Store 幂等写入
→ 标记 processed 或 failed
```

队列状态包括：

- `pending`：已经持久化，尚未成功投递；
- `processed`：行为 Store 已确认写入；
- `failed`：投递失败，保存错误类型和尝试次数，可按事件 ID 重放。

API 响应增加 `late`、`queue_status` 和 `replayed`，原有 `duplicate` 与 `event` 字段保持不变。

## 时间策略

- 事件发生时间距当前不超过 24 小时：正常处理；
- 超过 24 小时但不超过 30 天：标记 `late=true` 后继续处理；
- 超过 30 天：返回 HTTP 422；
- 事件时间领先服务端超过 5 分钟：返回 HTTP 422。

这里的 Watermark 是首个确定性基线。正式流处理平台需要根据业务转化周期、客户端离线时长和分区水位线重新配置。

## 机器人过滤

系统读取 HTTP `User-Agent`，匹配 `bot`、`spider`、`crawler`、`headless`、`selenium`、`python-requests`、`scrapy` 等明显自动化标识。命中后返回 HTTP 403，事件不会进入队列。

该规则只拦截明确机器人，不能替代网关风控、设备指纹、频率特征和异常行为模型。

## 持久化与配置

默认数据库路径：

```text
.runtime/behavior-events.sqlite3
```

目录已经加入 `.gitignore`。可以通过环境变量覆盖：

```dotenv
SEEKORA_BEHAVIOR_QUEUE_PATH=D:\seekora-data\behavior-events.sqlite3
```

SQLite 适合本地开发和单实例部署；多实例生产环境应通过同一个 `BehaviorEventQueue` 端口替换为 Kafka、Pulsar、云消息队列或数据库 Outbox。

## 新增文件职责

- `src/seekora_agent/domain/event_pipeline.py`：定义队列状态、队列条目和对外处理结果。
- `src/seekora_agent/application/event_pipeline.py`：实现机器人过滤、迟到分类、先入队后投递、失败标记和幂等重放。
- `src/seekora_agent/infrastructure/stores/sqlite_event_queue.py`：实现可跨进程重启保留状态的 SQLite 队列适配器。
- `tests/test_event_pipeline.py`：覆盖迟到事件、异常时间、机器人过滤、冲突、失败重放和 SQLite 恢复。
- `docs/01-architecture/event-pipeline.md`：记录处理顺序、时间策略、配置方式和生产替换边界。

## 当前边界

- API 请求内仍同步完成队列投递，没有独立后台 Consumer；
- `replay()` 已提供应用服务能力，尚未开放无认证管理接口；
- SQLite 队列持久化，但行为聚合 Store 仍是内存实现；
- 尚未实现分区、水位线推进、批量消费、死信队列和指数退避；
- 删除 Profile 会同时删除队列原始载荷，生产消息平台还需实现删除传播或密钥擦除。
