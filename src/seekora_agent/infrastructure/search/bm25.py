from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from ...domain.models import Constraint, Item, SearchQuery, SearchResult


ASCII_TOKEN = re.compile(r"[a-z0-9]+(?:[._+-][a-z0-9]+)*")
CJK_RUN = re.compile(r"[\u3400-\u9fff]+")


def tokenize(text: str) -> list[str]:
    normalized = text.lower()
    tokens = ASCII_TOKEN.findall(normalized)
    for run in CJK_RUN.findall(normalized):
        tokens.extend(run)
        tokens.extend(run[index:index + 2] for index in range(len(run) - 1))
    return tokens


def _compare(actual: Any, constraint: Constraint) -> bool:
    if actual is None:
        return False
    expected = constraint.value
    try:
        if constraint.operator == "eq":
            return actual == expected
        if constraint.operator == "in":
            return actual in expected
        if constraint.operator == "lte":
            return actual <= expected
        if constraint.operator == "gte":
            return actual >= expected
    except TypeError:
        return False
    return False


def item_is_allowed(item: Item, query: SearchQuery) -> bool:
    if item.tenant_id != query.tenant_id or item.status != "active":
        return False
    if item.permission_tags:
        if not set(item.permission_tags).intersection(query.allowed_permission_tags):
            return False
    return all(
        _compare(item.field_value(rule.field), rule)
        for rule in query.constraints if rule.is_active()
    )


class BM25Baseline:
    def __init__(self, items: list[Item], k1: float = 1.5, b: float = 0.75):
        self.items = items
        self.k1 = k1
        self.b = b
        self.term_frequencies = [Counter(tokenize(item.searchable_text())) for item in items]
        self.lengths = [sum(frequencies.values()) for frequencies in self.term_frequencies]
        self.average_length = sum(self.lengths) / max(len(self.lengths), 1)
        self.document_frequency: Counter[str] = Counter()
        for frequencies in self.term_frequencies:
            self.document_frequency.update(frequencies.keys())

    def _idf(self, token: str) -> float:
        document_count = len(self.items)
        frequency = self.document_frequency[token]
        return math.log(1 + (document_count - frequency + 0.5) / (frequency + 0.5))

    def _score(self, document_index: int, query_tokens: list[str]) -> float:
        frequencies = self.term_frequencies[document_index]
        length = self.lengths[document_index]
        score = 0.0
        for token in set(query_tokens):
            term_frequency = frequencies[token]
            if term_frequency == 0:
                continue
            denominator = term_frequency + self.k1 * (
                1 - self.b + self.b * length / max(self.average_length, 1)
            )
            score += self._idf(token) * term_frequency * (self.k1 + 1) / denominator
        return score

    def search(self, query: SearchQuery, top_k: int = 10) -> list[SearchResult]:
        query_tokens = tokenize(query.text)
        results: list[SearchResult] = []
        for index, item in enumerate(self.items):
            if not item_is_allowed(item, query):
                continue
            lexical_score = self._score(index, query_tokens)
            title_bonus = 1.0 if query.text.lower() in item.title.lower() else 0.0
            quality_bonus = max(0.0, min(item.quality_score, 1.0)) * 0.05
            final_score = lexical_score + title_bonus + quality_bonus
            if final_score <= 0:
                continue
            results.append(SearchResult(
                item=item,
                score=final_score,
                reasons=("bm25", "hard_constraints_passed"),
            ))
        results.sort(key=lambda result: (-result.score, result.item.item_id))
        return results[:top_k]
