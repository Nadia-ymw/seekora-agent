"""In-memory authoritative catalog repository used for final validation."""

from __future__ import annotations

from ..domain.models import Item


class InMemoryCatalogRepository:
    def __init__(self, items: list[Item]) -> None:
        self._items = {item.item_id: item for item in items}

    async def get(self, item_id: str) -> Item | None:
        return self._items.get(item_id)
