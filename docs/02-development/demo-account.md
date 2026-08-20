# 本地测试账户

项目启动时会初始化一个仅用于端到端联调的账户：

```text
tenant_id: demo
user_id: seekora-demo-user
default_session_id: seekora-demo-session
display_name: Seekora 测试用户
```

读取当前测试账户：

```text
GET /agent/dev/account
```

账户预置以下显式 Profile：

- 正向偏好：`轻薄`、`长续航`；
- 负向偏好：`厚重`；
- `personalization_enabled=true`；
- `behavior_storage_enabled=true`。

因此可以直接使用该身份测试查询结果曝光、反馈写入、持久化队列和行为召回：

```json
{
  "query": "推荐一台轻薄笔记本",
  "tenant_id": "demo",
  "user_id": "seekora-demo-user",
  "session_id": "seekora-demo-session"
}
```

## 安全边界

- 该账户没有密码、Token、注册、登录、角色或权限管理；
- `/agent/dev/account` 是只读开发接口，不是认证接口；
- 账户仍只拥有公共目录权限，不能绕过 Item ACL；
- 测试账户开启 Consent 只为了覆盖联调链路，其他新用户仍默认关闭；
- Profile 使用内存存储，删除后会在下次进程启动时重新初始化；
- 生产部署必须禁用开发账户和该接口，并接入可信认证网关。

## 新增文件职责

- `src/seekora_agent/domain/test_account.py`：定义无认证凭据的测试账户，并集中维护默认身份和初始 Profile。
- `tests/test_demo_account.py`：验证账户初始化、授权画像、新用户隐私默认值和无认证秘密。
- `docs/02-development/demo-account.md`：提供账户字段、使用示例、安全边界和文件职责说明。

`InMemoryProfileStore` 同时增加了显式初始 Profile 注入能力。它只接受启动层传入的数据，不会为普通用户自动开启授权。
