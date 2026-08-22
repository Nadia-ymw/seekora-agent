# 本地开发指南

## 环境要求

- Conda 环境：`nanobot`
- Python 3.11+
- PowerShell 或兼容终端

## 安装

```powershell
conda env create -f environment.yml
conda activate nanobot
python -m pip install -e ".[dev]"
```

## 运行测试

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python -m unittest discover -s tests -v
```

不激活环境时：

```powershell
conda run -n nanobot python -m unittest discover -s tests -v
```

## 离线命令

安装后可以使用统一脚本：

```powershell
seekora-agent quality
seekora-agent search --query "适合编程的轻薄本"
seekora-agent evaluate --golden data/golden/queries.jsonl
```

构建版本化 Embedding 索引和启用语义 Challenger 的命令见[本地语义模型配置](semantic-models.md)。默认开发和测试不安装或加载模型权重。

也可以直接运行模块：

```powershell
python -m seekora_agent.interfaces.cli quality
```

处理本地下载的 KuaiSearch-Lite 电子产品数据：

```powershell
python -m seekora_agent.interfaces.cli prepare-kuaisearch `
  --source data/external/kuaisearch/items_lite/train.jsonl `
  --output data/processed/kuaisearch-electronics/items.jsonl `
  --report data/processed/kuaisearch-electronics/report.json `
  --category-level1-id 30 `
  --limit 50000
```

原始数据、处理结果和完整字段映射见 [KuaiSearch-Lite 电子产品数据处理](kuaisearch-data.md)。

## 启动 API

```powershell
conda run -n nanobot python -m uvicorn seekora_agent.bootstrap:app --host 127.0.0.1 --port 8000
```

默认目录为 `data/processed/kuaisearch-electronics/items.jsonl`。路径不存在、
文件为空或包含重复商品 ID 时启动会明确失败，不会静默回退到样例目录。
如需显式使用其他目录：

```powershell
$env:SEEKORA_CATALOG_PATH = "C:\data\items.jsonl"
```

单元测试或最小演示可显式传入 `data/sample/items.jsonl`。

行为事件队列默认保存在 `.runtime/behavior-events.sqlite3`。如需把运行数据放到独立目录：

```powershell
$env:SEEKORA_BEHAVIOR_QUEUE_PATH = "D:\seekora-data\behavior-events.sqlite3"
$env:SEEKORA_PROFILE_DB_PATH = "D:\seekora-data\long-term-profiles.sqlite3"
$env:SEEKORA_REQUEST_REPLAY_DB_PATH = "D:\seekora-data\request-replays.sqlite3"
$env:SEEKORA_SESSION_DB_PATH = "D:\seekora-data\sessions.sqlite3"
$env:SEEKORA_RECEIPT_DB_PATH = "D:\seekora-data\receipts.sqlite3"
```

Session 默认保留 24 小时并最多保存 40 条消息，Receipt 默认保留 30 天。可分别用 `SEEKORA_SESSION_TTL_SECONDS`、`SEEKORA_SESSION_MAX_MESSAGES` 和 `SEEKORA_RECEIPT_RETENTION_SECONDS` 调整。

接口文档地址为 `http://127.0.0.1:8000/docs`。
Web 测试台地址为 `http://127.0.0.1:8000/`。

读取预置的本地测试账户：

```powershell
curl.exe http://127.0.0.1:8000/agent/dev/account
```

该账户没有密码或登录能力，详细字段和安全边界见[本地测试账户](demo-account.md)。

## 可选 LLM 意图解析

默认使用规则解析器，不需要 API Key。启用 OpenAI、配置模型、保护密钥和验证降级的完整说明见 [LLM 配置与新增文件说明](llm-configuration.md)。

聊天页面的交互、SSE 事件和判断 DeepSeek 是否实际调用的方法见 [Web 测试台使用说明](frontend-testing.md)。
