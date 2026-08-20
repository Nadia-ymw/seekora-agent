"""LTR 训练样本、基础特征和时间切分结果领域契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


DatasetPartition = Literal["train", "validation", "test"]
LTR_FEATURE_SCHEMA_VERSION = "ltr-basic-v1"


@dataclass(frozen=True)
class LTRFeatureVector:
    """只包含曝光时可用的特征，防止行为结果泄漏到模型输入。"""

    catalog_score: float = 0.0
    vector_score: float = 0.0
    behavior_score: float = 0.0
    source_count: int = 0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "catalog_score": self.catalog_score,
            "vector_score": self.vector_score,
            "behavior_score": self.behavior_score,
            "source_count": self.source_count,
        }


@dataclass(frozen=True)
class LTRTrainingSample:
    """一个曝光商品对应一个分级相关性样本。"""

    tenant_id: str
    request_id: str
    exposure_id: str
    item_id: str
    exposed_at: str
    position: int
    label: int
    label_action: str | None
    model_version: str
    features: LTRFeatureVector
    feature_schema_version: str = LTR_FEATURE_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "request_id": self.request_id,
            "exposure_id": self.exposure_id,
            "item_id": self.item_id,
            "exposed_at": self.exposed_at,
            "position": self.position,
            "label": self.label,
            "label_action": self.label_action,
            "model_version": self.model_version,
            "feature_schema_version": self.feature_schema_version,
            "features": self.features.as_dict(),
        }


@dataclass(frozen=True)
class TimeSplitDataset:
    """按曝光时间切分后的互斥数据集。"""

    train: tuple[LTRTrainingSample, ...]
    validation: tuple[LTRTrainingSample, ...]
    test: tuple[LTRTrainingSample, ...]

    def partition(self, name: DatasetPartition) -> tuple[LTRTrainingSample, ...]:
        return getattr(self, name)
