from desktopmate_agent.rag.embeddings import FakeEmbeddingProvider
from desktopmate_agent.rag.milvus import MilvusCollectionConfig, MilvusVectorStore, build_milvus_schema
from desktopmate_agent.rag.models import KnowledgeChunk


def make_chunk(chunk_id: str, text: str, *, title: str = "Milvus 配置") -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        document_id="doc",
        text=text,
        chunk_index=0,
        start_char=0,
        end_char=len(text),
        metadata={
            "source_path": "/docs/milvus.md",
            "title": title,
            "heading_path": ["Milvus 配置"],
        },
    )


class FakeMilvusClient:
    def __init__(self) -> None:
        self.collections: set[str] = set()
        self.created_collection_args = None
        self.loaded_collections: list[str] = []
        self.inserted_rows: list[dict[str, object]] = []
        self.search_requests: list[dict[str, object]] = []
        self.search_rows: list[list[dict[str, object]]] = []

    def has_collection(self, collection_name: str) -> bool:
        return collection_name in self.collections

    def prepare_index_params(self):
        return FakeIndexParams()

    def create_collection(self, **kwargs):
        self.collections.add(kwargs["collection_name"])
        self.created_collection_args = kwargs

    def load_collection(self, collection_name: str, **kwargs):
        self.loaded_collections.append(collection_name)

    def insert(self, collection_name: str, data, **kwargs):
        self.inserted_rows.extend(data)
        return {"insert_count": len(data)}

    def search(self, collection_name: str, data=None, limit: int = 10, output_fields=None, **kwargs):
        self.search_requests.append(
            {
                "collection_name": collection_name,
                "data": data,
                "limit": limit,
                "output_fields": output_fields,
                "kwargs": kwargs,
            }
        )
        return self.search_rows


class FakeIndexParams:
    def __init__(self) -> None:
        self.field_name = "embedding"
        self.indexes: list[dict[str, object]] = []

    def add_index(self, **kwargs):
        self.indexes.append(kwargs)


def test_milvus_collection_config_defaults_match_chunk_model():
    config = MilvusCollectionConfig(collection_name="desktopmate_knowledge", embedding_dimensions=128)

    assert config.collection_name == "desktopmate_knowledge"
    assert config.embedding_dimensions == 128
    assert config.metric_type == "COSINE"
    assert config.index_type == "HNSW"


def test_build_milvus_schema_contains_required_chunk_fields():
    config = MilvusCollectionConfig(collection_name="desktopmate_knowledge", embedding_dimensions=128)

    schema = build_milvus_schema(config)

    assert schema["collection_name"] == "desktopmate_knowledge"
    assert schema["fields"]["chunk_id"]["primary_key"] is True
    assert schema["fields"]["embedding"]["dimension"] == 128
    assert set(schema["fields"]) == {
        "chunk_id",
        "document_id",
        "text",
        "source_path",
        "title",
        "heading_path",
        "embedding",
    }
    assert schema["index"] == {
        "field_name": "embedding",
        "metric_type": "COSINE",
        "index_type": "HNSW",
        "params": {"M": 8, "efConstruction": 64},
    }


def test_milvus_vector_store_creates_collection_and_loads_it():
    client = FakeMilvusClient()
    config = MilvusCollectionConfig(collection_name="desktopmate_knowledge", embedding_dimensions=16)

    MilvusVectorStore(
        config=config,
        embedding_provider=FakeEmbeddingProvider(dimensions=16),
        client=client,
    )

    assert client.created_collection_args["collection_name"] == "desktopmate_knowledge"
    assert client.created_collection_args["schema"].description == "DesktopMate knowledge chunks"
    assert client.created_collection_args["index_params"].field_name == "embedding"
    assert client.loaded_collections == ["desktopmate_knowledge"]


def test_milvus_vector_store_add_chunks_inserts_embeddings_and_metadata():
    client = FakeMilvusClient()
    store = MilvusVectorStore(
        config=MilvusCollectionConfig(collection_name="desktopmate_knowledge", embedding_dimensions=16),
        embedding_provider=FakeEmbeddingProvider(dimensions=16),
        client=client,
    )
    chunk = make_chunk("milvus:1", "先启动 Docker，再创建 collection。")

    store.add_chunks([chunk])

    assert client.inserted_rows[0]["chunk_id"] == "milvus:1"
    assert client.inserted_rows[0]["source_path"] == "/docs/milvus.md"
    assert len(client.inserted_rows[0]["embedding"]) == 16


def test_milvus_vector_store_search_maps_rows_to_retrieval_results():
    client = FakeMilvusClient()
    client.search_rows = [
        [
            {
                "chunk_id": "milvus:1",
                "document_id": "doc",
                "text": "先启动 Docker，再创建 collection。",
                "source_path": "/docs/milvus.md",
                "title": "Milvus 配置",
                "heading_path": ["Milvus 配置"],
                "distance": 0.91,
            }
        ]
    ]
    store = MilvusVectorStore(
        config=MilvusCollectionConfig(collection_name="desktopmate_knowledge", embedding_dimensions=16),
        embedding_provider=FakeEmbeddingProvider(dimensions=16),
        client=client,
    )

    results = store.search("怎么配置 Milvus", limit=1)

    assert results[0].chunk.chunk_id == "milvus:1"
    assert results[0].chunk.metadata["heading_path"] == ["Milvus 配置"]
    assert results[0].score == 0.91
