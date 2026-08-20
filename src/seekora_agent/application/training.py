"""从可信曝光和行为事件构建 LTR 样本，并执行防泄漏时间切分。"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Iterable

from ..domain.behavior import BehaviorEvent
from ..domain.exposure import ExposureRecord
from ..domain.training import LTRFeatureVector, LTRTrainingSample, TimeSplitDataset


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("training timestamps must include a timezone")
    return parsed


class LTRTrainingSampleBuilder:
    """以曝光为负样本基座，在固定窗口内聚合最强行为标签。"""

    LABEL_GAINS = {
        "click": 1,
        "favorite": 2,
        "conversion": 3,
        "dismiss": 0,
    }

    def __init__(self, attribution_window: timedelta = timedelta(days=7)) -> None:
        if attribution_window <= timedelta(0):
            raise ValueError("attribution_window must be positive")
        self.attribution_window = attribution_window

    def build(
        self,
        exposures: Iterable[ExposureRecord],
        events: Iterable[BehaviorEvent],
        *,
        as_of: datetime,
    ) -> tuple[LTRTrainingSample, ...]:
        if as_of.tzinfo is None:
            raise ValueError("as_of must include a timezone")
        events_by_item: defaultdict[
            tuple[str, str, str], list[BehaviorEvent]
        ] = defaultdict(list)
        for event in events:
            events_by_item[(event.tenant_id, event.exposure_id, event.item_id)].append(event)

        samples: list[LTRTrainingSample] = []
        ordered_exposures = sorted(
            exposures, key=lambda item: (_parse_time(item.created_at), item.exposure_id)
        )
        for exposure in ordered_exposures:
            exposed_at = _parse_time(exposure.created_at)
            window_end = exposed_at + self.attribution_window
            # 归因窗口未闭合时不能把“暂时无行为”误标成稳定负样本。
            if window_end > as_of:
                continue
            for item in sorted(exposure.items, key=lambda value: value.position):
                matching = [
                    event
                    for event in events_by_item[
                        (exposure.tenant_id, exposure.exposure_id, item.item_id)
                    ]
                    if self._belongs_to_exposure(event, exposure)
                    and exposed_at <= _parse_time(event.occurred_at) <= window_end
                    and event.action in self.LABEL_GAINS
                ]
                strongest = min(
                    matching,
                    key=lambda event: (
                        -self.LABEL_GAINS[event.action],
                        _parse_time(event.occurred_at),
                        event.event_id,
                    ),
                    default=None,
                )
                scores = dict(item.source_scores)
                samples.append(
                    LTRTrainingSample(
                        tenant_id=exposure.tenant_id,
                        request_id=exposure.request_id,
                        exposure_id=exposure.exposure_id,
                        item_id=item.item_id,
                        exposed_at=exposure.created_at,
                        position=item.position,
                        label=self.LABEL_GAINS[strongest.action] if strongest else 0,
                        label_action=strongest.action if strongest else None,
                        model_version=exposure.model_version,
                        features=LTRFeatureVector(
                            catalog_score=float(scores.get("catalog_search", 0.0)),
                            vector_score=float(scores.get("vector_search", 0.0)),
                            behavior_score=float(scores.get("behavior_recall", 0.0)),
                            source_count=len(item.recall_sources),
                        ),
                    )
                )
        return tuple(samples)

    @staticmethod
    def _belongs_to_exposure(
        event: BehaviorEvent, exposure: ExposureRecord
    ) -> bool:
        """再次检查身份字段，离线导入不能绕过在线归因约束。"""
        return (
            event.user_id == exposure.user_id
            and event.session_id == exposure.session_id
            and event.request_id == exposure.request_id
        )


class TimeBasedDatasetSplitter:
    """使用固定时间边界切分，保证同一曝光不会跨数据集。"""

    def split(
        self,
        samples: Iterable[LTRTrainingSample],
        *,
        train_end: datetime,
        validation_end: datetime,
    ) -> TimeSplitDataset:
        if train_end.tzinfo is None or validation_end.tzinfo is None:
            raise ValueError("split boundaries must include a timezone")
        if train_end >= validation_end:
            raise ValueError("train_end must be earlier than validation_end")

        partitions: dict[str, list[LTRTrainingSample]] = {
            "train": [],
            "validation": [],
            "test": [],
        }
        for sample in sorted(
            samples,
            key=lambda item: (_parse_time(item.exposed_at), item.exposure_id, item.position),
        ):
            exposed_at = _parse_time(sample.exposed_at)
            if exposed_at < train_end:
                partition = "train"
            elif exposed_at < validation_end:
                partition = "validation"
            else:
                partition = "test"
            partitions[partition].append(sample)
        return TimeSplitDataset(
            train=tuple(partitions["train"]),
            validation=tuple(partitions["validation"]),
            test=tuple(partitions["test"]),
        )
