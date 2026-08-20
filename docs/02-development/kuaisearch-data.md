# KuaiSearch-Lite 电子产品数据处理

## 1. 目录约定

大型外部数据不进入 Git，目录约定如下：

```text
data/
├── external/kuaisearch/items_lite/train.jsonl   # 下载的 2.79 GB 原始商品表
├── processed/kuaisearch-electronics/items.jsonl # 转换后的开发目录
├── processed/kuaisearch-electronics/report.json # 转换统计与内容哈希
├── golden/                                      # 仓库内的小型固定评测集
└── sample/                                      # 仓库内的小型测试目录
```

`.gitignore` 只忽略 `data/external/` 和 `data/processed/`，现有的 `data/sample/` 与 `data/golden/` 继续纳入版本控制。

## 2. 当前处理范围

当前使用 KuaiSearch-Lite 一级类目 `30 / 手机/数码/电脑办公`。完整扫描结果为：

- 原始商品：6,634,118 条；
- 匹配电子数码商品：362,049 条；
- 确定性开发样本：50,000 条；
- 无效记录：0 条；
- 输出文件：约 35.8 MiB。

匹配到的二级类目包括手机及配件、影音娱乐、数码配件、智能设备、电脑外设、网络设备、电脑整机配件、摄影摄像和电子教育。原始分布中“手机及配件”占比较高，后续训练或评测应按二级类目分层报告，必要时再做分层采样。

## 3. 转换命令

```powershell
$env:PYTHONPATH = "src"
python -m seekora_agent.interfaces.cli prepare-kuaisearch `
  --source data/external/kuaisearch/items_lite/train.jsonl `
  --output data/processed/kuaisearch-electronics/items.jsonl `
  --report data/processed/kuaisearch-electronics/report.json `
  --category-level1-id 30 `
  --limit 50000 `
  --seed 20260819
```

转换器逐行读取原始 JSONL，只在内存中保留最多 `limit` 条候选。采样键由固定 Seed 和 `item_id` 计算，不依赖输入进程的随机状态；相同输入、参数和代码会生成相同内容哈希。

## 4. 字段映射

| KuaiSearch 字段 | Seekora 字段 |
|---|---|
| `item_id` | 带 `kuaisearch-` 前缀的 `item_id` |
| `item_title` | `title` |
| 最细非 UNKNOWN 类目 | `category` |
| 品牌、店铺、三级类目路径 | `description` 和 `attributes` |
| 固定值 | `tenant_id=demo`、`status=active`、`permission_tags=[public]` |

原始数据没有价格、库存、ACL、更新时间或质量分。为支持本地约束测试，转换器会根据商品类型和稳定哈希生成明确标记的测试值，包括价格、库存、近 30 天销量、评分、评论数和使用场景；手机、平板和电脑还会生成内存、存储等类型相关规格，笔记本额外包含屏幕尺寸、重量和续航。

所有合成记录均包含 `synthetic_test_data=true` 和 `synthetic_fields`，这些值只用于测试，不能作为 KuaiSearch 商品的真实事实或用于业务分析。`updated_at` 使用版本化快照时间，`quality_score` 固定为 0。

当价格或规格进入约束证据时，Receipt 和 SSE 结果中的 `trust_level` 为 `synthetic`，不会伪装成 `authoritative` 权威目录事实。

默认租户使用 Web 测试台兼容的 `demo`，因此设置 `SEEKORA_CATALOG_PATH` 后可以直接查询；如需隔离测试，可通过转换命令的 `--tenant` 参数改为其他租户。

直接启动测试台：

```powershell
$env:SEEKORA_CATALOG_PATH = "data/processed/kuaisearch-electronics/items.jsonl"
python -m uvicorn seekora_agent.bootstrap:app --host 127.0.0.1 --port 8000
```

可以使用“8000 元以内、16GB 内存以上、续航 8 小时以上、重量 2kg 以内的笔记本”等查询验证多条件过滤。

## 5. 新增文件职责

- `src/seekora_agent/infrastructure/kuaisearch.py`：流式解析、类目筛选、确定性限量采样、字段映射、原子写入和统计报告；
- `tests/test_kuaisearch.py`：验证类目过滤、Seekora Schema、质量检查、采样复现和源/目标路径保护；
- 本文档：记录本地目录、处理命令、真实统计、字段语义和数据边界。

生成文件被 Git 忽略。如需在另一台机器复现，必须重新下载原始文件并执行相同命令，不应提交大型 JSONL。
