from collections import Counter
from math import log

from desktopmate_agent.rag.embeddings import _tokenize
from desktopmate_agent.rag.models import KnowledgeChunk
from desktopmate_agent.rag.vector_store import RetrievalResult


class InMemoryKeywordIndex:
    def __init__(self) -> None:
        self._items: list[tuple[KnowledgeChunk, Counter[str]]] = []

    def add_chunks(self, chunks: list[KnowledgeChunk]) -> None:
        self._items.extend((chunk, Counter(_tokens_for_chunk(chunk))) for chunk in chunks)

    def search(self, query: str, *, limit: int = 5) -> list[RetrievalResult]:
        if limit < 1:
            raise ValueError("limit must be positive")
        query_terms = Counter(_tokenize(query))
        ranked = sorted(
            (
                RetrievalResult(chunk=chunk, score=_term_overlap_score(query_terms, terms))
                for chunk, terms in self._items
            ),
            key=lambda result: (-result.score, result.chunk.chunk_id),
        )
        return [result for result in ranked if result.score > 0][:limit]


class InMemoryBM25KeywordIndex:
    def __init__(self, *, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._items: list[tuple[KnowledgeChunk, Counter[str], int]] = []
        self._document_frequency: Counter[str] = Counter()
        self._average_document_length = 0.0

    def add_chunks(self, chunks: list[KnowledgeChunk]) -> None:
        for chunk in chunks:
            terms = Counter(_tokens_for_chunk(chunk))
            self._items.append((chunk, terms, sum(terms.values())))
            self._document_frequency.update(terms.keys())
        self._average_document_length = _average_length([length for _, _, length in self._items])

    def search(self, query: str, *, limit: int = 5) -> list[RetrievalResult]:
        if limit < 1:
            raise ValueError("limit must be positive")
        query_terms = Counter(_tokenize(query))
        ranked = sorted(
            (
                RetrievalResult(chunk=chunk, score=self._bm25_score(query_terms, terms, length))
                for chunk, terms, length in self._items
            ),
            key=lambda result: (-result.score, result.chunk.chunk_id),
        )
        return [result for result in ranked if result.score > 0][:limit]

    def _bm25_score(self, query_terms: Counter[str], chunk_terms: Counter[str], length: int) -> float:
        score = 0.0
        document_count = len(self._items)
        if document_count == 0:
            return score
        for term, query_count in query_terms.items():
            term_frequency = chunk_terms.get(term, 0)
            if term_frequency == 0:
                continue
            document_frequency = self._document_frequency[term]
            idf = log(1 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5))
            denominator = term_frequency + self.k1 * (
                1 - self.b + self.b * length / max(self._average_document_length, 1.0)
            )
            score += query_count * idf * (term_frequency * (self.k1 + 1)) / denominator
        return score


def _tokens_for_chunk(chunk: KnowledgeChunk) -> list[str]:
    metadata_text = " ".join(str(value) for value in chunk.metadata.values())
    return _tokenize(f"{chunk.chunk_id} {chunk.text} {metadata_text}")


def _term_overlap_score(query_terms: Counter[str], chunk_terms: Counter[str]) -> float:
    return float(
        sum(min(query_count, chunk_terms.get(term, 0)) for term, query_count in query_terms.items())
    )


def _average_length(lengths: list[int]) -> float:
    if not lengths:
        return 0.0
    return sum(lengths) / len(lengths)
