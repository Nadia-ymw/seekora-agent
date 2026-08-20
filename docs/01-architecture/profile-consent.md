# Session Intent、Profile 与 Consent

本增量把“当前请求需要什么”和“用户长期偏好什么”拆成两个生命周期不同的对象，避免系统把一次查询自动固化成用户画像。

## 数据边界

- `SessionIntentSnapshot`：保存最近一次已解析意图，只属于当前会话，用于后续多轮任务衔接。
- `LongTermProfile`：只保存用户显式提交的正向与负向偏好，按 `tenant_id + user_id` 隔离。
- `ConsentState`：分别控制个性化与行为数据保存，两个开关默认都关闭。

意图解析完成后，Runtime 只更新 Session Intent，不会向 Profile 写入任何推断结果。长期偏好写入前必须启用 `personalization_enabled`；关闭该授权后，数据仍可由用户查询或删除，但 `ranking_snapshot()` 不会把它提供给排序链路。

`behavior_storage_enabled` 作为独立授权控制曝光、点击等事件的写入；行为是否能参与召回还必须再次检查 `personalization_enabled`。详细链路见 [行为反馈与授权召回](behavior-feedback.md)。

## 本地 API

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/agent/profiles/{user_id}?tenant_id=...` | 查询画像与授权状态 |
| `PUT` | `/agent/profiles/{user_id}/consent?tenant_id=...` | 更新两个授权开关 |
| `PUT` | `/agent/profiles/{user_id}/preferences?tenant_id=...` | 覆盖显式偏好，未授权返回 409 |
| `DELETE` | `/agent/profiles/{user_id}?tenant_id=...` | 删除画像 |

这些接口面向本地演示。生产环境不能信任客户端提交的 `tenant_id` 和 `user_id`，必须由认证网关根据已验证身份注入。

## 新增文件职责

- `src/seekora_agent/domain/profile.py`：定义授权状态和长期画像两个领域对象。
- `src/seekora_agent/application/profile.py`：定义画像存储端口，并集中实现授权校验、偏好规范化和排序读取边界。
- `tests/test_profile.py`：覆盖默认拒绝、显式授权、租户隔离、排序屏蔽和删除语义。
- `docs/01-architecture/profile-consent.md`：记录 Session/Profile 数据边界、授权规则与 API 契约。

当前使用内存存储，进程重启后数据会丢失；生产化时可通过 `ProfileStore` 端口替换为数据库实现，而不改变应用服务规则。
