"""Core domain entities shared by every application layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

# 定义支持的查询操作符：等于、包含于、小于等于、大于等于
Operator = Literal["eq", "in", "lte", "gte"]
# 约束的作用域，用于区分该约束是跟随上下文（如当前对话）、会话（整个用户会话）还是用户身份（长期画像）
ConstraintScope = Literal["contextual", "session", "identity"]
# 约束的来源，表明它来自用户查询（query）、会话历史（session）、用户画像（profile）还是系统强制（system）。来源影响信任度和生命周期
ConstraintSource = Literal["query", "session", "profile", "system"]
# 约束的状态，控制其是否生效（active）、被临时挂起（suspended）或已过期（expired）
ConstraintStatus = Literal["active", "suspended", "expired"]

# 商品检索文档只纳入稳定且有业务语义的字段。顺序同时用于 BM25、TF-IDF 和
# Embedding 内容哈希，不能依赖 attributes 的插入顺序或混入内部 ID、合成标记。
SEARCHABLE_ATTRIBUTE_FIELDS = (
    "brand",
    "seller",
    "category_level1_name",
    "category_level2_name",
    "category_level3_name",
    "product_type",
    "use_cases",
)

# 约束与查询文本分离，支持结构化过滤 + 全文检索的组合查询
@dataclass(frozen=True)
class Constraint:
    field: str
    operator: Operator
    value: Any
    scope: ConstraintScope = "session"  # 作用域，默认为会话级
    source: ConstraintSource = "query"  # 来源，默认为用户查询
    source_turn: int = 0                # 若来源为对话轮次，指示第几轮产生的约束（用于衰减或时效）
    confidence: float = 1.0
    status: ConstraintStatus = "active"
    priority: int = 100                 # 优先级（数字越大优先级越高），用于冲突消解
    expires_at: datetime | None = None  # 绝对过期时间，为 None 表示永不过期

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Constraint":
        operator = raw["operator"]
        if operator not in {"eq", "in", "lte", "gte"}:
            raise ValueError(f"unsupported constraint operator: {operator}")
        scope = str(raw.get("scope", "session"))
        source = str(raw.get("source", "query"))
        status = str(raw.get("status", "active"))
        if scope not in {"contextual", "session", "identity"}:
            raise ValueError(f"unsupported constraint scope: {scope}")
        if source not in {"query", "session", "profile", "system"}:
            raise ValueError(f"unsupported constraint source: {source}")
        if status not in {"active", "suspended", "expired"}:
            raise ValueError(f"unsupported constraint status: {status}")
        expires_at = raw.get("expires_at")
        parsed_expiry = (
            datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if expires_at else None
        )
        confidence = float(raw.get("confidence", 1.0))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("constraint confidence must be between 0 and 1")
        priority = int(raw.get("priority", 100))
        if priority < 0:
            raise ValueError("constraint priority must be non-negative")
        return cls(
            field=str(raw["field"]),
            operator=operator,
            value=raw["value"],
            scope=scope,  # type: ignore[arg-type]
            source=source,  # type: ignore[arg-type]
            source_turn=max(0, int(raw.get("source_turn", 0))),
            confidence=confidence,
            status=status,  # type: ignore[arg-type]
            priority=priority,
            expires_at=parsed_expiry,
        )

    def is_active(self, at: datetime | None = None) -> bool:
        if self.status != "active":
            return False
        if self.expires_at is None:
            return True
        current = at or datetime.now(UTC)
        expiry = self.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        return expiry > current

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "operator": self.operator,
            "value": self.value,
            "scope": self.scope,
            "source": self.source,
            "source_turn": self.source_turn,
            "confidence": self.confidence,
            "status": self.status,
            "priority": self.priority,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

# 表示被搜索的实体对象（如商品、文档、知识条目）
@dataclass(frozen=True)
class Item:
    item_id: str
    tenant_id: str    # 租户/组织标识，实现数据隔离
    title: str
    description: str
    category: str
    attributes: dict[str, Any]   # 动态扩展属性，如价格、尺寸等
    status: str                  # 业务状态（如上架/下架），具体取值由业务定义
    permission_tags: tuple[str, ...]  # 权限标签，用于访问控制（元组保证不可变）
    updated_at: datetime
    quality_score: float = 0.0        # 质量分数（默认 0.0），可用于排序加权

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
        values: list[str] = [self.title]
        for field_name in SEARCHABLE_ATTRIBUTE_FIELDS:
            value = self.attributes.get(field_name)
            if isinstance(value, (list, tuple, set)):
                values.extend(str(item) for item in value if str(item).strip())
            elif value is not None and str(value).strip():
                values.append(str(value))
        values.extend((self.category, self.description))
        return " ".join(value.strip() for value in values if value.strip())

    def field_value(self, name: str) -> Any:
        if hasattr(self, name):
            return getattr(self, name)
        return self.attributes.get(name)

# 表示一次完整的搜索请求
@dataclass(frozen=True)
class SearchQuery:
    text: str                      # 用户输入的查询文本，用于全文检索
    tenant_id: str                 # 租户 ID
    allowed_permission_tags: tuple[str, ...] = ()  # 当前用户被允许的权限标签，用于过滤结果
    constraints: tuple[Constraint, ...] = ()       # 一组结构化约束（可来自不同来源）

# 表示标注好的理想查询，用于评估或基准测试（通常用于学习排序或离线评估）
@dataclass(frozen=True)
class GoldenQuery:
    query_id: str        
    query: SearchQuery         # 完整的查询对象（含约束）
    relevance: dict[str, int] = field(default_factory=dict)     # 相关性标注：item_id -> 相关度分数（整数，越高越相关）

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

# 表示一条搜索结果，封装命中的 Item 及其相关性信息
# 将结果与解释绑定，便于后续展示分析，也为多阶段排序提供可追溯性
@dataclass(frozen=True)
class SearchResult:
    item: Item     # 命中的实体对象
    score: float   # 计算得到的相关性分数（越高越相关）
    reasons: tuple[str, ...]    # 解释性文本，记录为何给出该分数
