# LLM 配置与新增文件说明

## 1. 本增量实现了什么

OpenAI 模式同时用于首轮 `ResolvedIntent` 解析和多轮 `ConstraintPatch` 解析。模型只负责理解自然语言，Session 归并、召回、硬约束校验、排序和目录复核仍由确定性代码执行。

```text
SEEKORA_INTENT_RESOLVER=rules
├─ RuleBasedIntentResolver（默认，不需要 API Key）
└─ RuleBasedSessionContextPatchResolver

SEEKORA_INTENT_RESOLVER=openai
├─ ChatOpenAI → 结构化 LLMIntentOutput → ResolvedIntent
│  └─ 调用或结构校验失败 → RuleBasedIntentResolver
└─ ChatOpenAI → 结构化 ConstraintPatch → 确定性 Reducer
   └─ 调用、结构或安全校验失败 → RuleBasedSessionContextPatchResolver
```

系统不会自动选择或猜测模型名。启用 OpenAI 时，必须同时明确配置 `OPENAI_API_KEY` 和 `OPENAI_MODEL`。

## 2. 新增文件及作用

### `.env.example`

环境变量模板，只列出变量名和安全默认值，不包含真实密钥。开发者复制为 `.env` 后填写本机配置；`.env` 已被 `.gitignore` 排除，不应提交到版本库。

### `config/settings.py`

使用 `pydantic-settings` 统一读取并校验配置：

- 默认选择 `rules`，保证无密钥环境可启动；
- 用 `SecretStr` 保存 API Key，降低日志或调试输出误泄漏风险；
- 启用 OpenAI 时检查 Key 和模型名是否完整；
- `safe_summary()` 只输出“是否配置密钥”，从不输出密钥内容；
- 校验超时时间和最大重试次数的取值范围。

### `infrastructure/llm/openai.py`

OpenAI 的 LangChain ChatModel 工厂。它把经过校验的配置转换成 `ChatOpenAI`，集中设置模型、超时、重试、温度和可选 Base URL。该文件只负责 Provider SDK 适配，不负责 Prompt 或业务判断。

### `infrastructure/intent/langchain_llm.py`

LLM 意图解析适配器：

- `LLMIntentOutput` 和 `LLMConstraint` 限定模型必须返回的结构；
- `INTENT_PROMPT` 要求只从用户原文提取约束，并把用户文本视为不可信数据；
- `with_structured_output()` 将 ChatModel 输出约束为 Pydantic Schema；
- 数值单位和字段类型在进入领域层前再次标准化；
- Provider 超时、输出不合法或转换失败时回退到规则解析器。

### `infrastructure/session_context/langchain_llm.py`

多轮请求的结构化 Patch 解析器。它只能输出 `new_task/follow_up` 和 `set/add/remove/clear`，不能直接写 Session，也不能输出租户或权限字段。详细边界见[多轮 Session 约束上下文](../01-architecture/session-context.md)。

### `tests/test_llm_intent.py`

在不访问网络的情况下验证配置安全、缺失配置报错、结构化结果映射和失败回退。测试用 `RunnableLambda` 模拟模型响应，因此不会消耗真实 Token 或 API 额度。

### `bootstrap.py` 的装配变化

`build_intent_resolver()` 和 `build_session_context_resolver()` 是两个装配入口。`build_runtime()` 根据同一个 `SEEKORA_INTENT_RESOLVER` 开关注入规则或 OpenAI 实现；应用层和领域层无需感知具体供应商。

## 3. 在 `seekora-agent` 环境中配置

复制模板：

```powershell
conda activate seekora-agent
Copy-Item .env.example .env
```

编辑 `.env`：

```dotenv
SEEKORA_INTENT_RESOLVER=openai
OPENAI_API_KEY=你的真实密钥
OPENAI_MODEL=你要使用的模型ID
OPENAI_TIMEOUT_SECONDS=30
OPENAI_MAX_RETRIES=2
```

`OPENAI_BASE_URL` 仅在使用兼容网关或代理时填写；直接调用 OpenAI 时保持为空。OpenAI Python SDK 默认从 `OPENAI_API_KEY` 环境变量读取密钥，参考 [OpenAI API 官方文档](https://developers.openai.com/api/reference/python/resources/fine_tuning/subresources/jobs/subresources/checkpoints/methods/list)。

也可以仅在当前 PowerShell 会话中设置，避免在磁盘保存密钥：

```powershell
$env:SEEKORA_INTENT_RESOLVER = "openai"
$env:OPENAI_API_KEY = "你的真实密钥"
$env:OPENAI_MODEL = "你要使用的模型ID"
```

然后启动：

```powershell
conda run -n seekora-agent python -m uvicorn seekora_agent.bootstrap:app --host 127.0.0.1 --port 8000
```

恢复无模型的默认模式：

```dotenv
SEEKORA_INTENT_RESOLVER=rules
```

## 4. 密钥安全要求

- 不要把真实 Key 写入 `.env.example`、源码、测试、日志或 Receipt；
- 不要把 `.env` 提交到 Git；
- CI/生产环境应使用平台 Secret Manager 注入环境变量；
- 开发、测试和生产分别使用独立 Key，并设置额度与权限边界；
- Key 一旦出现在提交、截图或日志中，应立即撤销并重新生成。

## 5. 当前边界

- LLM 当前只用于意图和多轮 Patch 结构化，不负责最终状态执行或结果真实性；
- OpenAI 模式的第二轮及后续请求通常包含意图和 Patch 两次模型调用；
- 回退规则保证可用性，但复杂表达在降级后可能降低解析质量；
- 尚未实现 Provider 调用指标、Prompt 版本持久化和熔断器；
- 真正的模型质量和延迟必须使用脱敏 Golden Set、受控测试 Key 和独立测试环境评估。
