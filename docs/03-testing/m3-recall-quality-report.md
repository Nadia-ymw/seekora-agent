# M3 本地混合召回质量门禁报告

> 测量日期：2026-08-22  
> Catalog：`data/processed/kuaisearch-electronics/items.jsonl`  
> 快照 SHA-256：`e1063931609d4bb40a7121499344e82e06ab92a043e050f9997c712357f7abfb`

## 结论

BM25+Qwen Active 通过了暖态延迟、零结果和稳定性门槛，但 Recall@10、MRR@10 和 NDCG@10 均低于 BM25+TF-IDF 基线，因此本轮总门禁失败。`SEEKORA_EMBEDDING_MODE` 必须继续保持 `challenger`，不得切换本地默认链路。

## 方法

- 固定查询集：`data/golden/processed-recall-queries.jsonl`；
- 查询数：13，覆盖 processed 报告中的 13 类主要电子商品意图；
- 候选标注池：BM25+TF-IDF 与 BM25+Qwen 各自 Top 5 并集；
- 相关性：依据标题、权威分类和原始描述人工复核，分为 1～3 级；
- Top K：10；每种链路暖态重复 3 轮，共 39 个延迟样本；
- 两个召回源按线上行为并行执行，再使用 `k=60` 的 RRF 融合；
- 模型冷加载单独记录，不计入暖态 P95。

复现命令：

```powershell
$env:PYTHONPATH = "src"
conda run -n nanobot python -m seekora_agent.interfaces.cli compare-recall `
  --catalog data/processed/kuaisearch-electronics/items.jsonl `
  --golden data/golden/processed-recall-queries.jsonl `
  --vector-index .runtime/qwen3-embedding-0.6b-vectors.sqlite3 `
  --device cuda `
  --runs 3
```

## 结果

Recall@10、MRR@10、NDCG@10 的公式、计算示例、指标差异，以及 Qwen RRF 权重的具体作用见
[M3 搜索推荐检索策略与测试说明报告](m3-search-retrieval-development-summary.md#43-质量指标)。

| 指标 | BM25+TF-IDF | BM25+Qwen Active | 差值 |
|---|---:|---:|---:|
| Recall@10 | 0.851282 | 0.691026 | -0.160256 |
| MRR@10 | 0.938462 | 0.884615 | -0.053846 |
| NDCG@10 | 0.744787 | 0.667385 | -0.077402 |
| 零结果率 | 0 | 0 | 0 |
| 暖态 P50 | 722.310 ms | 637.593 ms | -84.717 ms |
| 暖态 P95 | 1077.472 ms | 1052.042 ms | -25.430 ms |
| 最大暖态延迟 | 1092.393 ms | 1123.949 ms | +31.556 ms |
| 排序稳定率 | 1.0 | 1.0 | 0 |

Qwen 模型冷加载加首查为 10,591.549 ms。Embedding 来源版本为：

```text
sqlite-vec-cosine-v1:qwen3:Qwen/Qwen3-Embedding-0.6B@main:dim=1024:query=8fe95552ceb5
```

## 门禁判定

| 门禁 | 结果 |
|---|---|
| 最少 10 条固定查询 | 通过（13 条） |
| Active 暖态 P95 ≤ 2 秒 | 通过（1.052 秒） |
| 零结果率不增加 | 通过 |
| 同配置重复排序稳定 | 通过（1.0） |
| Recall/MRR/NDCG 均不退化 | 失败 |
| 总门禁 | **失败** |

## 初步误差观察与下一步

当前 Qwen 对自然语言和同义表达有补充能力，但在包含明确品类词和硬属性词的查询中，等权 RRF 会把部分语义相近但品类错误或缺少关键属性的商品推入前列，例如“8000mAh 长续航户外手机”召回移动电源。

### 加权 RRF 开发集试验

新增 `processed-recall-development.jsonl` 作为独立查询表达开发集，其候选池由开发查询自身的 BM25+TF-IDF 与 BM25+Qwen Top 5 并集构成并单独复核。扫描结果如下：

| Qwen 权重 | Recall@10 | MRR@10 | NDCG@10 |
|---:|---:|---:|---:|
| 0.25 | 0.747253 | 0.833333 | 0.682061 |
| 0.50 | 0.679029 | 0.820513 | 0.652095 |
| 0.75 | 0.679029 | 0.897436 | 0.677201 |
| 1.00 | **0.777015** | 0.895604 | **0.722958** |
| 1.25 | 0.671245 | 0.895604 | 0.673361 |
| 1.50 | 0.660256 | 0.895604 | 0.668799 |
| 2.00 | 0.660256 | 0.857143 | 0.652053 |

开发集按 NDCG、Recall、MRR 顺序选择后仍得到权重 1.0。留出集指标与首轮等权结果一致，说明简单来源降权或升权不能修复当前质量问题。`SEEKORA_QWEN_RRF_WEIGHT` 保留为可回退配置，但本地值不修改。

下一步依次评估：

1. 把确定性识别出的商品类型传入召回契约，对 BM25 与 Qwen 做一致的类别收窄；
2. 扩大向量候选后再执行可信 Catalog 类别复核，避免过滤后候选不足；
3. 保持租户、状态、ACL 和硬约束后置复核不变；
4. 只在未参与类别规则开发的留出集重新执行同一门禁。
