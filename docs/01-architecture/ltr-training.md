# 曝光行为训练样本与 LTR 特征契约

## 1. 目标

本增量把已完成归因的曝光和行为事件转换为可重复构建的 Learning to Rank（LTR）样本，并使用固定时间边界切分训练、验证和测试集。它只负责数据契约和离线样本构建，不在在线请求中加载或执行排序模型。

## 2. 样本和标签

每个曝光商品生成一个样本，以 `exposure_id + item_id` 作为自然关联键。默认归因窗口为 7 天：

- 无有效后续行为或 `dismiss`：标签 0；
- `click`：标签 1；
- `favorite`：标签 2；
- `conversion`：标签 3；
- 同一商品出现多个行为时取最高等级，等级相同则按事件时间和 `event_id` 稳定选择。

生成器会再次校验租户、用户、会话和请求关联。窗口外事件、身份不匹配事件以及纯 `exposure` 事件不参与标签。只有当 `曝光时间 + 归因窗口 <= as_of` 时才生成样本，避免把尚未成熟的曝光错误标为负样本。

## 3. 基础特征契约

`ltr-basic-v1` 只使用曝光发生时已经保存的召回分数：

| 特征 | 含义 |
|---|---|
| `catalog_score` | 关键词目录召回分数 |
| `vector_score` | 语义召回分数 |
| `behavior_score` | 授权行为召回分数 |
| `source_count` | 命中召回源数量 |

曝光位置保留为样本元数据，便于后续做位置偏差诊断或倾向校正，但不直接进入基础模型特征，避免模型简单复制旧排序。未知召回源不会被静默映射成已有特征，但仍计入 `source_count`。

## 4. 时间切分

`TimeBasedDatasetSplitter` 接受带时区的 `train_end` 和 `validation_end`：

```text
exposed_at < train_end                         → train
train_end <= exposed_at < validation_end       → validation
validation_end <= exposed_at                   → test
```

切分基于曝光时间而不是行为时间。同一曝光中的全部商品拥有相同曝光时间，因此不会跨数据集。边界由调用方固定并记录，避免每次运行按随机比例产生不可回放的数据集。

## 5. 新增和变更文件职责

- `src/seekora_agent/domain/training.py`：定义版本化 LTR 特征、训练样本和时间切分结果，不依赖应用或基础设施层；
- `src/seekora_agent/application/training.py`：实现标签聚合、成熟窗口检查、归因复核和确定性时间切分；
- `tests/test_training.py`：覆盖分级标签、曝光时特征、未成熟样本、无效归因、窗口外行为和互斥时间切分；
- `src/seekora_agent/domain/exposure.py`：在曝光商品中保存不可变的召回源分数快照；
- `src/seekora_agent/application/exposure.py`：登记曝光时从最终结果复制并排序召回源分数，供后续训练重放。

## 6. 当前边界

- 尚未提供生产数据仓库读取器和训练集文件导出任务；
- 尚未做位置倾向估计、负样本降采样和跨用户/会话泄漏审计；
- 尚未训练或上线 LTR 模型，模型发布、Shadow 和分群护栏属于后续增量；
- 训练作业必须只读取仍具备合法处理依据且未被隐私删除的数据。
