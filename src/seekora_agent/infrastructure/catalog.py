from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..config.defaults import DEFAULT_CATALOG_RELATIVE_PATH
from ..domain.models import GoldenQuery, Item


DEFAULT_CATALOG_PATH = Path(DEFAULT_CATALOG_RELATIVE_PATH)
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def resolve_catalog_path(path: str | Path = DEFAULT_CATALOG_PATH) -> Path:
    """把配置中的相对目录固定解析到项目根，避免入口工作目录改变数据快照。"""
    resolved = Path(path).expanduser()
    return resolved if resolved.is_absolute() else PROJECT_ROOT / resolved


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
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(
            f"catalog file does not exist: {source}. "
            "Run prepare-kuaisearch or explicitly configure SEEKORA_CATALOG_PATH."
        )
    items = [Item.from_dict(raw) for raw in _read_jsonl(source)]
    if not items:
        raise ValueError(f"catalog file contains no valid items: {source}")
    seen: set[str] = set()
    duplicate_ids: set[str] = set()
    for item in items:
        if item.item_id in seen:
            duplicate_ids.add(item.item_id)
        seen.add(item.item_id)
    if duplicate_ids:
        preview = ", ".join(sorted(duplicate_ids)[:5])
        raise ValueError(f"catalog contains duplicate item_id values: {preview}")
    return items


def catalog_snapshot_sha256(path: str | Path) -> str:
    """计算原始 JSONL 快照哈希，供运行日志、Receipt 和索引元数据复用。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
