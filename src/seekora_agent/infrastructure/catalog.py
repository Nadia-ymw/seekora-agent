from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..domain.models import GoldenQuery, Item


def _read_jsonl(path: str | Path) -> Iterable[dict]:
    with Path(path).open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at {path}:{line_number}: {exc}") from exc


def load_items(path: str | Path) -> list[Item]:
    return [Item.from_dict(raw) for raw in _read_jsonl(path)]


def load_golden_queries(path: str | Path) -> list[GoldenQuery]:
    return [GoldenQuery.from_dict(raw) for raw in _read_jsonl(path)]


@dataclass(frozen=True)
class QualityReport:
    total_items: int
    duplicate_ids: tuple[str, ...]
    inactive_items: int
    missing_title: int
    missing_category: int
    missing_attributes: int

    @property
    def passed(self) -> bool:
        return not self.duplicate_ids and self.missing_title == 0 and self.missing_category == 0

    def as_dict(self) -> dict:
        return {
            "total_items": self.total_items,
            "duplicate_ids": list(self.duplicate_ids),
            "inactive_items": self.inactive_items,
            "missing_title": self.missing_title,
            "missing_category": self.missing_category,
            "missing_attributes": self.missing_attributes,
            "passed": self.passed,
        }


def inspect_quality(items: list[Item]) -> QualityReport:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in items:
        if item.item_id in seen:
            duplicates.add(item.item_id)
        seen.add(item.item_id)
    return QualityReport(
        total_items=len(items),
        duplicate_ids=tuple(sorted(duplicates)),
        inactive_items=sum(item.status != "active" for item in items),
        missing_title=sum(not item.title.strip() for item in items),
        missing_category=sum(not item.category.strip() for item in items),
        missing_attributes=sum(not item.attributes for item in items),
    )
