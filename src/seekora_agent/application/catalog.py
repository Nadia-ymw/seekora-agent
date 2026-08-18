"""Catalog access port used for final authoritative candidate validation."""

from __future__ import annotations

from typing import Protocol

from ..domain.models import Item


class CatalogRepository(Protocol):
    async def get(self, item_id: str) -> Item | None: ...
