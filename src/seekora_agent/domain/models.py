"""Core domain entities shared by every application layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

# 定义支持的查询操作符：等于、包含于、小于等于、大于等于
Operator = Literal["eq", "in", "lte", "gte"]

# 约束与查询文本分离，支持结构化过滤 + 全文检索的组合查询
@dataclass(frozen=True)
class Constraint:
    field: str
    operator: Operator
    value: Any

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Constraint":
        operator = raw["operator"]
        if operator not in {"eq", "in", "lte", "gte"}:
            raise ValueError(f"unsupported constraint operator: {operator}")
        return cls(field=str(raw["field"]), operator=operator, value=raw["value"])


@dataclass(frozen=True)
class Item:
    item_id: str
    tenant_id: str
    title: str
    description: str
    category: str
    attributes: dict[str, Any]
    status: str
    permission_tags: tuple[str, ...]
    updated_at: datetime
    quality_score: float = 0.0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Item":
        required = {
            "item_id", "tenant_id", "title", "description", "category",
            "attributes", "status", "permission_tags", "updated_at",
        }
        missing = sorted(required - raw.keys())
        if missing:
            raise ValueError(f"missing required item fields: {', '.join(missing)}")
        return cls(
            item_id=str(raw["item_id"]),
            tenant_id=str(raw["tenant_id"]),
            title=str(raw["title"]),
            description=str(raw["description"]),
            category=str(raw["category"]),
            attributes=dict(raw["attributes"]),
            status=str(raw["status"]),
            permission_tags=tuple(str(tag) for tag in raw["permission_tags"]),
            updated_at=datetime.fromisoformat(str(raw["updated_at"]).replace("Z", "+00:00")),
            quality_score=float(raw.get("quality_score", 0.0)),
        )

    def searchable_text(self) -> str:
        values = [self.title, self.description, self.category]
        values.extend(str(value) for value in self.attributes.values())
        return " ".join(values)

    def field_value(self, name: str) -> Any:
        if hasattr(self, name):
            return getattr(self, name)
        return self.attributes.get(name)


@dataclass(frozen=True)
class SearchQuery:
    text: str
    tenant_id: str
    allowed_permission_tags: tuple[str, ...] = ()
    constraints: tuple[Constraint, ...] = ()


@dataclass(frozen=True)
class GoldenQuery:
    query_id: str
    query: SearchQuery
    relevance: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "GoldenQuery":
        query = SearchQuery(
            text=str(raw["query"]),
            tenant_id=str(raw["tenant_id"]),
            allowed_permission_tags=tuple(raw.get("allowed_permission_tags", [])),
            constraints=tuple(Constraint.from_dict(item) for item in raw.get("constraints", [])),
        )
        return cls(
            query_id=str(raw["query_id"]),
            query=query,
            relevance={str(key): int(value) for key, value in raw["relevance"].items()},
        )


@dataclass(frozen=True)
class SearchResult:
    item: Item
    score: float
    reasons: tuple[str, ...]
