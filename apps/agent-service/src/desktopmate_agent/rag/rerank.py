from collections import Counter
from typing import Protocol

from desktopmate_agent.rag.embeddings import _tokenize
from desktopmate_agent.rag.vector_store import RetrievalResult


class Reranker(Protocol):
    def rerank(
        self,
        query: str,
        results: list[RetrievalResult],
        *,
        limit: int,
    ) -> list[RetrievalResult]:
        pass


class RuleBasedReranker:
    def rerank(
        self,
        query: str,
        results: list[RetrievalResult],
        *,
        limit: int,
    ) -> list[RetrievalResult]:
        if limit < 1:
            raise ValueError("limit must be positive")
        query_terms = Counter(_tokenize(query))
        reranked = [
            RetrievalResult(
                chunk=result.chunk,
                score=result.score + _rerank_bonus(query_terms, result),
            )
            for result in results
        ]
        return sorted(reranked, key=lambda result: (-result.score, result.chunk.chunk_id))[:limit]


def _rerank_bonus(query_terms: Counter[str], result: RetrievalResult) -> float:
    text_terms = Counter(_tokenize(result.chunk.text))
    metadata_terms = Counter(_tokenize(_metadata_text(result)))
    text_hits = _overlap_count(query_terms, text_terms)
    metadata_hits = _overlap_count(query_terms, metadata_terms)
    return 0.2 * text_hits + 0.45 * metadata_hits


def _metadata_text(result: RetrievalResult) -> str:
    metadata_values = " ".join(str(value) for value in result.chunk.metadata.values())
    return f"{result.chunk.chunk_id} {metadata_values}"


def _overlap_count(left: Counter[str], right: Counter[str]) -> int:
    return sum(min(count, right.get(term, 0)) for term, count in left.items())
