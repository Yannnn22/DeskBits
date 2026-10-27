from desktopmate_agent.rag.embeddings import EmbeddingProvider
from desktopmate_agent.rag.keyword_index import InMemoryBM25KeywordIndex
from desktopmate_agent.rag.models import KnowledgeChunk
from desktopmate_agent.rag.rerank import Reranker
from desktopmate_agent.rag.strategies import RAGStrategy
from desktopmate_agent.rag.vector_store import InMemoryVectorStore, RetrievalResult


class HybridRetriever:
    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider,
        strategy: RAGStrategy,
        reranker: Reranker | None = None,
        vector_store: InMemoryVectorStore | None = None,
    ) -> None:
        self.strategy = strategy
        self.reranker = reranker
        self.vector_store = vector_store or InMemoryVectorStore(embedding_provider=embedding_provider)
        self.keyword_index = InMemoryBM25KeywordIndex()

    def add_chunks(
        self,
        chunks: list[KnowledgeChunk],
        embeddings: list[list[float]] | None = None,
    ) -> None:
        self.vector_store.add_chunks(chunks, embeddings=embeddings)
        self.keyword_index.add_chunks(chunks)

    def search(self, query: str, *, limit: int = 5) -> list[RetrievalResult]:
        candidate_limit = max(limit, self.strategy.rerank_top_n)
        vector_results = self.vector_store.search(query, limit=candidate_limit)
        keyword_results = self.keyword_index.search(query, limit=candidate_limit)
        # Keep vector similarity absolute. Normalising against the best result
        # makes an unrelated top hit look like a perfect match in an otherwise
        # empty query, which defeats knowledge_miss handling.
        vector_scores = {
            result.chunk.chunk_id: max(result.score, 0.0)
            for result in vector_results
        }
        keyword_scores = _normalized_scores(keyword_results)
        chunks = {
            result.chunk.chunk_id: result.chunk
            for result in [*vector_results, *keyword_results]
        }
        fused = [
            RetrievalResult(
                chunk=chunk,
                score=(
                    self.strategy.vector_weight * vector_scores.get(chunk_id, 0.0)
                    + self.strategy.keyword_weight * keyword_scores.get(chunk_id, 0.0)
                ),
            )
            for chunk_id, chunk in chunks.items()
        ]
        fused = sorted(fused, key=lambda result: (-result.score, result.chunk.chunk_id))
        if self.reranker is not None:
            fused = self.reranker.rerank(query, fused, limit=limit)
        else:
            fused = fused[:limit]
        return [
            result
            for result in fused
            if result.score >= self.strategy.minimum_score
        ]


def _normalized_scores(results: list[RetrievalResult]) -> dict[str, float]:
    if not results:
        return {}
    max_score = max(result.score for result in results)
    if max_score <= 0:
        return {result.chunk.chunk_id: 0.0 for result in results}
    return {result.chunk.chunk_id: result.score / max_score for result in results}
