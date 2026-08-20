import unittest
from datetime import UTC, datetime, timedelta

from seekora_agent.application.training import (
    LTRTrainingSampleBuilder,
    TimeBasedDatasetSplitter,
)
from seekora_agent.domain.behavior import BehaviorEvent
from seekora_agent.domain.exposure import ExposedItem, ExposureRecord


BASE_TIME = datetime(2026, 8, 1, tzinfo=UTC)


def exposure(exposure_id: str, days: int = 0) -> ExposureRecord:
    return ExposureRecord(
        exposure_id=exposure_id,
        tenant_id="demo",
        user_id="user-1",
        session_id=f"session-{exposure_id}",
        request_id=f"request-{exposure_id}",
        items=(
            ExposedItem(
                item_id="lap-1",
                position=0,
                recall_sources=("catalog_search", "vector_search"),
                source_scores=(("catalog_search", 0.8), ("vector_search", 0.6)),
            ),
            ExposedItem(item_id="lap-2", position=1),
        ),
        model_version="ranker-v1",
        created_at=(BASE_TIME + timedelta(days=days)).isoformat(),
    )


def event(
    source: ExposureRecord,
    action: str,
    *,
    event_id: str,
    item_id: str = "lap-1",
    after: timedelta = timedelta(hours=1),
    user_id: str = "user-1",
) -> BehaviorEvent:
    return BehaviorEvent(
        event_id=event_id,
        tenant_id=source.tenant_id,
        user_id=user_id,
        session_id=source.session_id,
        request_id=source.request_id,
        exposure_id=source.exposure_id,
        item_id=item_id,
        action=action,
        occurred_at=(datetime.fromisoformat(source.created_at) + after).isoformat(),
        position=0,
        recall_sources=("catalog_search", "vector_search"),
        model_version=source.model_version,
    )


class LTRTrainingSampleBuilderTest(unittest.TestCase):
    def test_builds_graded_labels_and_exposure_time_features(self):
        shown = exposure("exp-1")
        events = [
            event(shown, "click", event_id="click-1"),
            event(shown, "favorite", event_id="favorite-1", after=timedelta(days=1)),
        ]

        samples = LTRTrainingSampleBuilder().build(
            [shown], events, as_of=BASE_TIME + timedelta(days=8)
        )

        self.assertEqual(2, len(samples))
        self.assertEqual(("lap-1", 2, "favorite"), (
            samples[0].item_id, samples[0].label, samples[0].label_action
        ))
        self.assertEqual(0.8, samples[0].features.catalog_score)
        self.assertEqual(0.6, samples[0].features.vector_score)
        self.assertEqual(2, samples[0].features.source_count)
        self.assertEqual(("lap-2", 0, None), (
            samples[1].item_id, samples[1].label, samples[1].label_action
        ))

    def test_skips_unmatured_exposure_and_ignores_invalid_attribution(self):
        matured = exposure("matured")
        recent = exposure("recent", days=9)
        invalid = event(matured, "conversion", event_id="invalid", user_id="other-user")

        samples = LTRTrainingSampleBuilder().build(
            [matured, recent], [invalid], as_of=BASE_TIME + timedelta(days=10)
        )

        self.assertEqual({"matured"}, {sample.exposure_id for sample in samples})
        self.assertTrue(all(sample.label == 0 for sample in samples))

    def test_ignores_behavior_outside_attribution_window(self):
        shown = exposure("exp-1")
        too_late = event(
            shown, "conversion", event_id="late", after=timedelta(days=8)
        )

        samples = LTRTrainingSampleBuilder().build(
            [shown], [too_late], as_of=BASE_TIME + timedelta(days=10)
        )

        self.assertEqual(0, samples[0].label)


class TimeBasedDatasetSplitterTest(unittest.TestCase):
    def test_splits_complete_exposures_by_time(self):
        exposures = [exposure("train", 0), exposure("validation", 10), exposure("test", 20)]
        samples = LTRTrainingSampleBuilder(attribution_window=timedelta(days=1)).build(
            exposures, [], as_of=BASE_TIME + timedelta(days=30)
        )

        dataset = TimeBasedDatasetSplitter().split(
            samples,
            train_end=BASE_TIME + timedelta(days=5),
            validation_end=BASE_TIME + timedelta(days=15),
        )

        self.assertEqual({"train"}, {sample.exposure_id for sample in dataset.train})
        self.assertEqual(
            {"validation"}, {sample.exposure_id for sample in dataset.validation}
        )
        self.assertEqual({"test"}, {sample.exposure_id for sample in dataset.test})

    def test_rejects_invalid_split_boundaries(self):
        with self.assertRaises(ValueError):
            TimeBasedDatasetSplitter().split(
                [], train_end=BASE_TIME, validation_end=BASE_TIME
            )


if __name__ == "__main__":
    unittest.main()
