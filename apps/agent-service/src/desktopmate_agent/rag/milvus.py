from typing import Any, Literal

from pydantic import BaseModel, Field


class MilvusCollectionConfig(BaseModel):
    collection_name: str = Field(min_length=1)
    embedding_dimensions: int = Field(ge=1)
    uri: str = Field(default="http://127.0.0.1:19530", min_length=1)
    metric_type: Literal["COSINE", "IP", "L2"] = "COSINE"
    index_type: str = "HNSW"
    index_params: dict[str, int] = Field(default_factory=lambda: {"M": 8, "efConstruction": 64})


def build_milvus_schema(config: MilvusCollectionConfig) -> dict[str, Any]:
    return {
        "collection_name": config.collection_name,
        "fields": {
            "chunk_id": {"type": "VARCHAR", "max_length": 128, "primary_key": True},
            "document_id": {"type": "VARCHAR", "max_length": 128},
            "text": {"type": "VARCHAR", "max_length": 8192},
            "source_path": {"type": "VARCHAR", "max_length": 2048},
            "title": {"type": "VARCHAR", "max_length": 512},
            "heading_path": {"type": "JSON"},
            "embedding": {
                "type": "FLOAT_VECTOR",
                "dimension": config.embedding_dimensions,
            },
        },
        "index": {
            "field_name": "embedding",
            "metric_type": config.metric_type,
            "index_type": config.index_type,
            "params": config.index_params,
        },
    }


def build_milvus_collection_schema(config: MilvusCollectionConfig) -> Any:
    from pymilvus import CollectionSchema, DataType, FieldSchema

    fields = [
        FieldSchema(
            name="chunk_id",
            dtype=DataType.VARCHAR,
            description="chunk id",
            is_primary=True,
            max_length=128,
        ),
        FieldSchema(name="document_id", dtype=DataType.VARCHAR, description="document id", max_length=128),
        FieldSchema(name="text", dtype=DataType.VARCHAR, description="chunk text", max_length=8192),
        FieldSchema(name="source_path", dtype=DataType.VARCHAR, description="source path", max_length=2048),
        FieldSchema(name="title", dtype=DataType.VARCHAR, description="title", max_length=512),
        FieldSchema(name="heading_path", dtype=DataType.JSON, description="heading path"),
        FieldSchema(
            name="embedding",
            dtype=DataType.FLOAT_VECTOR,
            description="chunk embedding",
            dim=config.embedding_dimensions,
        ),
    ]
    return CollectionSchema(fields=fields, description="DesktopMate knowledge chunks")


class MilvusVectorStore:
    def __init__(
        self,
        *,
        config: MilvusCollectionConfig,
        embedding_provider: Any,
        client: Any | None = None,
    ) -> None:
        self.config = config
        self.embedding_provider = embedding_provider
        self.schema = build_milvus_schema(config)
        self.client = client or self._create_client(config)
        if not self.client.has_collection(config.collection_name):
            self.client.create_collection(
                collection_name=config.collection_name,
                schema=build_milvus_collection_schema(config),
                index_params=self._build_index_params(),
            )
        self.client.load_collection(config.collection_name)

    def add_chunks(
        self,
        chunks: list[Any],
        embeddings: list[list[float]] | None = None,
    ) -> None:
        if embeddings is None:
            embeddings = self.embedding_provider.embed_documents([chunk.text for chunk in chunks])
        rows = [
            self._serialize_chunk(chunk, embedding)
            for chunk, embedding in zip(chunks, embeddings, strict=True)
        ]
        self.client.insert(collection_name=self.config.collection_name, data=rows)

    def search(self, query: str, *, limit: int = 5) -> list[Any]:
        query_embedding = self.embedding_provider.embed_query(query)
        results = self.client.search(
            collection_name=self.config.collection_name,
            data=[query_embedding],
            limit=limit,
            output_fields=["chunk_id", "document_id", "text", "source_path", "title", "heading_path"],
            anns_field="embedding",
            search_params={"metric_type": self.config.metric_type, "params": {"ef": 64}},
        )
        if not results:
            return []
        return [_to_retrieval_result(row) for row in results[0]]

    def _create_client(self, config: MilvusCollectionConfig) -> Any:
        try:
            from pymilvus import MilvusClient
        except ImportError as exc:
            raise RuntimeError(
                "pymilvus is required for MilvusVectorStore. "
                "Install it before enabling DESKTOPMATE_MILVUS_ENABLED."
            ) from exc
        return MilvusClient(uri=config.uri)

    def _build_index_params(self) -> Any:
        index_params = self.client.prepare_index_params()
        index_params.add_index(
            field_name="embedding",
            index_type=self.config.index_type,
            metric_type=self.config.metric_type,
            params=self.config.index_params,
        )
        return index_params

    def _serialize_chunk(self, chunk: Any, embedding: list[float]) -> dict[str, Any]:
        return {
            "chunk_id": chunk.chunk_id,
            "document_id": chunk.document_id,
            "text": chunk.text,
            "source_path": chunk.metadata.get("source_path", ""),
            "title": chunk.metadata.get("title", ""),
            "heading_path": chunk.metadata.get("heading_path", []),
            "embedding": embedding,
        }


def _to_retrieval_result(row: dict[str, Any]) -> Any:
    from desktopmate_agent.rag.models import KnowledgeChunk
    from desktopmate_agent.rag.vector_store import RetrievalResult

    payload = row.get("entity", row)
    text = payload.get("text", "")
    chunk = KnowledgeChunk(
        chunk_id=str(payload.get("chunk_id", "")),
        document_id=str(payload.get("document_id", "")),
        text=text,
        chunk_index=0,
        start_char=0,
        end_char=len(text),
        metadata={
            "source_path": payload.get("source_path", ""),
            "title": payload.get("title", ""),
            "heading_path": payload.get("heading_path", []),
        },
    )
    score = float(row.get("distance", row.get("score", 0.0)))
    return RetrievalResult(chunk=chunk, score=score)
