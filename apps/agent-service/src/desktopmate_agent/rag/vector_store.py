from pydantic import BaseModel

from desktopmate_agent.rag.embeddings import EmbeddingProvider
from desktopmate_agent.rag.models import KnowledgeChunk


class RetrievalResult(BaseModel):
    chunk: KnowledgeChunk
    score: float


class InMemoryVectorStore:
    def __init__(self, *, embedding_provider: EmbeddingProvider) -> None:
        self.embedding_provider = embedding_provider
        self._items: list[tuple[KnowledgeChunk, list[float]]] = []

    def add_chunks(
        self,
        chunks: list[KnowledgeChunk],
        embeddings: list[list[float]] | None = None,
    ) -> None:
        if embeddings is None:
            embeddings = self.embedding_provider.embed_documents([chunk.text for chunk in chunks])
        self._items.extend(zip(chunks, embeddings, strict=True))

    def search(self, query: str, *, limit: int = 5) -> list[RetrievalResult]:
        if limit < 1:
            raise ValueError("limit must be positive")
        query_embedding = self.embedding_provider.embed_query(query)
        ranked = sorted(
            (
                RetrievalResult(chunk=chunk, score=_dot_product(query_embedding, embedding))
                for chunk, embedding in self._items
            ),
            key=lambda result: (-result.score, result.chunk.chunk_id),
        )
        return ranked[:limit]


def _dot_product(left: list[float], right: list[float]) -> float:
    return sum(left_value * right_value for left_value, right_value in zip(left, right, strict=True))
