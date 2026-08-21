# 行为反馈与授权召回

本增量建立“曝光/行为事件 → 幂等保存 → 授权读取 → 查询相关性约束 → RRF 融合”的最小闭环。行为数据默认不可写、不可用，两个动作分别受 Consent 控制。

## 授权边界

- 写入事件必须开启 `behavior_storage_enabled`；
- 行为召回必须同时开启 `behavior_storage_enabled` 和 `personalization_enabled`；
- 关闭授权后不再写入或读取，但用户仍可通过删除 Profile 清理历史行为；
- 删除 Profile API 会同步删除同租户、同用户的行为事件；
- 行为候选仍需通过租户、商品状态和 ACL 校验。

“允许保存行为”和“允许用于个性化”是两个独立决定，系统不能用其中一个替代另一个。

## 事件与幂等语义

`BehaviorEvent` 支持 `exposure`、`click`、`favorite`、`dismiss` 和 `conversion`。事件携带 `event_id`、`request_id`、`exposure_id`、Item、位置、召回源、模型版本和发生时间，用于后续归因与审计。反馈写入前必须通过[服务端曝光清单校验](exposure-validation.md)。

`event_id` 在单个租户内唯一：

- 首次提交返回 HTTP 201 和 `duplicate=false`；
- 相同业务载荷再次提交返回 HTTP 200 和 `duplicate=true`；
- 同一 ID 对应不同载荷返回 HTTP 409，不允许覆盖旧事件。

本地接口：

```text
POST /agent/feedback
```

当前请求中的身份字段用于本地测试；生产环境必须由认证网关注入可信的 `tenant_id` 和 `user_id`。

## 行为召回规则

行为分数采用可解释的初始权重：曝光 `0`、点击 `1`、收藏 `3`、负反馈 `-4`、转化 `5`。聚合分数小于等于零的商品不会召回。

为避免历史行为破坏当前查询相关性，`behavior_recall` 不能独立引入商品，只能提升同一次请求中已被关键词或语义召回命中的商品。最终结果仍经过 Constraint Engine 和 Catalog 复核。

## 新增文件职责

- `src/seekora_agent/domain/behavior.py`：定义不可变行为事件、支持的 Action 和幂等写入结果。
- `src/seekora_agent/application/behavior.py`：定义存储端口，实现写入授权、双重读取授权、行为聚合和删除传播。
- `src/seekora_agent/infrastructure/tools/behavior_recall.py`：把授权行为分数包装成 LangChain Tool，并执行目录状态与 ACL 校验。
- `tests/test_behavior.py`：覆盖授权、幂等、冲突、双重 Consent、ACL、查询相关性和删除传播。
- `docs/01-architecture/behavior-feedback.md`：记录行为事件、授权边界、召回规则和当前限制。

## 当前限制

- 使用内存事件存储，重启后数据丢失；
- 已校验 `request_id/exposure_id` 与曝光清单；浏览器真实可见性确认尚未实现；
- 已实现本地 SQLite 队列、迟到分类和基础机器人过滤；行为聚合 Store 与窗口衰减仍待生产化；
- 固定权重只是安全基线，不能替代经过离线和在线评测的开源 Cross-Encoder 或轻量重排服务；
- 行为工具只在默认 Bootstrap 中装配，生产实现需要数据库或事件平台适配器。
