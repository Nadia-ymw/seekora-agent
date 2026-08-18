# 本地开发指南

## 环境要求

- Conda 环境：`seekora-agent`
- Python 3.11+
- PowerShell 或兼容终端

## 安装

```powershell
conda env create -f environment.yml
conda activate seekora-agent
python -m pip install -e ".[dev]"
```

## 运行测试

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python -m unittest discover -s tests -v
```

不激活环境时：

```powershell
conda run -n seekora-agent python -m unittest discover -s tests -v
```

## 离线命令

安装后可以使用统一脚本：

```powershell
seekora-agent quality --catalog data/sample/items.jsonl
seekora-agent search --catalog data/sample/items.jsonl --query "适合编程的轻薄本"
seekora-agent evaluate --catalog data/sample/items.jsonl --golden data/golden/queries.jsonl
```

也可以直接运行模块：

```powershell
python -m seekora_agent.interfaces.cli quality --catalog data/sample/items.jsonl
```

## 启动 API

```powershell
conda run -n seekora-agent python -m uvicorn seekora_agent.bootstrap:app --host 127.0.0.1 --port 8000
```

默认目录为 `data/sample/items.jsonl`。替换目录：

```powershell
$env:SEEKORA_CATALOG_PATH = "C:\data\items.jsonl"
```

接口文档地址为 `http://127.0.0.1:8000/docs`。
Web 测试台地址为 `http://127.0.0.1:8000/`。

## 可选 LLM 意图解析

默认使用规则解析器，不需要 API Key。启用 OpenAI、配置模型、保护密钥和验证降级的完整说明见 [LLM 配置与新增文件说明](llm-configuration.md)。

聊天页面的交互、SSE 事件和判断 DeepSeek 是否实际调用的方法见 [Web 测试台使用说明](frontend-testing.md)。
