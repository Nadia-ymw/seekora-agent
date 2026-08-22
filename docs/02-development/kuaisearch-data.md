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
- 排除手机壳商品：210,488 条；
- 确定性开发样本：50,000 条；
- 无效记录：0 条；
- 输出文件：约 60.8 MiB（包含按类型补造的规格字段）。

匹配到的二级类目包括手机及配件、影音娱乐、数码配件、智能设备、电脑外设、网络设备、电脑整机配件、摄影摄像和电子教育。转换器默认在采样前排除 `phone_case`，避免手机壳淹没其他电子商品；报告中的 `excluded_rows`、`excluded_product_type_counts` 和 `product_type_counts` 分别记录排除结果与最终类型分布。

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

原始数据没有价格、库存、ACL、更新时间或质量分。为支持本地约束测试，转换器会根据 `item_id + 字段命名空间` 的稳定哈希生成明确标记的测试值，包括价格、库存、近 30 天销量、评分、评论数、使用场景、保修期、发货时效和退换期限。

类型相关规格按 `product_type` 分配：手机、平板和电脑包含内存、存储、屏幕或电池；显示器包含分辨率、面板和刷新率；音频商品包含连接方式、蓝牙、续航和降噪；相机、存储、网络、键鼠、智能设备和贴膜分别生成各自适用的规格。相同输入、参数与代码会得到完全一致的字段值。

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
