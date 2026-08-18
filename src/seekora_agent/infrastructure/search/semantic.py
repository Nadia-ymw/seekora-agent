"""Deterministic in-memory semantic challenger based on TF-IDF cosine similarity."""

from __future__ import annotations

import math
from collections import Counter

from ...domain.models import Item, SearchQuery, SearchResult
from .bm25 import item_is_allowed, tokenize


class InMemorySemanticIndex:
    """A testable vector-search placeholder; production should use embeddings + ANN."""

    def __init__(self, items: list[Item]) -> None:
        self.items = items
        self.documents = [Counter(tokenize(item.searchable_text())) for item in items]
        document_frequency: Counter[str] = Counter()
        for document in self.documents:
            document_frequency.update(document.keys())
        count = max(len(items), 1)
        self.idf = {
            token: math.log((count + 1) / (frequency + 1)) + 1
            for token, frequency in document_frequency.items()
        }
        self.vectors = [self._vector(document) for document in self.documents]

    def _vector(self, terms: Counter[str]) -> dict[str, float]:
        weighted = {token: frequency * self.idf.get(token, 1.0) for token, frequency in terms.items()}
        norm = math.sqrt(sum(value * value for value in weighted.values())) or 1.0
        return {token: value / norm for token, value in weighted.items()}

    def search(self, query: SearchQuery, top_k: int = 10) -> list[SearchResult]:
        query_vector = self._vector(Counter(tokenize(query.text)))
        results: list[SearchResult] = []
        for item, vector in zip(self.items, self.vectors, strict=True):
            if not item_is_allowed(item, query):
                continue
            score = sum(value * vector.get(token, 0.0) for token, value in query_vector.items())
            if score > 0:
                results.append(SearchResult(item, score, ("semantic_tfidf",)))
        results.sort(key=lambda result: (-result.score, result.item.item_id))
        return results[:top_k]
