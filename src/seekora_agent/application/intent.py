"""Intent-resolution port consumed by the Agent runtime."""

from __future__ import annotations

from typing import Protocol

from ..domain.fast_path import ResolvedIntent


class IntentResolver(Protocol):
    async def resolve(self, query: str) -> ResolvedIntent: ...
