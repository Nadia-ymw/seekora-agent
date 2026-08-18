# Web 测试台使用说明

## 1. 页面目标

Web 测试台提供类似 GPT 官网的单页聊天体验，用于验证当前 Fast Path 是否成功调用模型，以及后续召回、约束和 Receipt 链路是否正常。页面与 FastAPI 同源提供，不需要单独启动前端服务，也不需要配置 CORS。

访问地址：`http://127.0.0.1:8000/`

## 2. 新增文件职责

### `interfaces/http/static/index.html`

定义页面语义结构，包括会话侧栏、模型连接状态、欢迎页、消息区、快捷测试问题、输入框和停止按钮。文件不包含密钥或后端地址，所有接口均使用同源相对路径。

### `interfaces/http/static/styles.css`

实现桌面端和移动端响应式视觉：深色会话侧栏、轻色内容区、模型状态标签、执行步骤、意图约束卡片、推荐结果和 Receipt 详情。页面不依赖外部字体、图片或 CDN，离线环境也能完整显示。

### `interfaces/http/static/app.js`

负责页面交互和 API 调用：

- `GET /health` 检查服务状态；
- `GET /agent/config` 获取不含密钥的解析器版本；
- `POST /agent/query` 发送查询并使用 Fetch Stream 解析 SSE；
- 根据 `intent.resolved`、`recall.completed`、`constraints.applied`、`result` 更新执行进度；
- `POST /agent/requests/{request_id}/cancel` 请求取消；
- `GET /agent/receipts/{request_id}` 展示完整执行凭据；
- 使用浏览器本地存储保存当前 `session_id`，新建对话时生成新 Session。

### `interfaces/http/api.py` 的变化

- `GET /` 返回聊天页面；
- `/static` 提供 CSS 和 JavaScript；
- `GET /agent/config` 仅返回框架和 `resolver_version`，不返回 API Key；
- 原有查询、取消和 Receipt 接口保持不变。

## 3. 启动与测试模型

确认 `.env` 包含：

```dotenv
SEEKORA_INTENT_RESOLVER=openai
OPENAI_API_KEY=你的DeepSeek密钥
OPENAI_MODEL=deepseek-v4-pro
OPENAI_BASE_URL=https://api.deepseek.com
```

启动服务：

```powershell
conda run -n seekora-agent python -m uvicorn seekora_agent.bootstrap:app --host 127.0.0.1 --port 8000
```

浏览器打开 `http://127.0.0.1:8000/`，输入测试问题。判断是否调用模型时，不只看返回结果，应检查以下位置：

1. 页面右上角显示 `deepseek-v4-pro`；
2. 回复中的解析器标签显示 `deepseek-v4-pro`；
3. 意图面板中的 `resolver_version` 对应 `langchain-openai:deepseek-v4-pro`；
4. 执行凭据中的 `resolved_intent.resolver_version` 不是 `rules-zh-v1`。

如果显示“规则解析器”，说明系统没有启用模型，或者模型调用/结构化输出失败后触发了规则回退。此时查看服务终端，并检查 `.env`、账户余额、网络和模型权限。

## 4. 页面事件映射

| 后端事件 | 页面表现 |
|---|---|
| `request.accepted` | 显示请求已接收 |
| `intent.resolved` | 展示解析器、意图、置信度和硬约束 |
| `recall.started/completed` | 更新并行召回状态和候选数量 |
| `constraints.applied` | 展示通过目录复核的候选数量 |
| `result` | 渲染推荐卡片、RRF 分数和召回原因 |
| `error/cancelled` | 显示错误或取消原因 |
| `done` | 拉取并展示 Receipt |

## 5. 当前边界

- 页面使用固定演示租户 `demo` 和公开权限；
- Session 仅保存在当前浏览器和后端内存中，服务重启后后端历史会丢失；
- 当前结果是结构化推荐卡片，不是模型生成的长篇自然语言回答；
- 样例目录规模较小，只适合验证调用链路；
- API Key 只由后端 `.env` 读取，绝不会发送给浏览器。
